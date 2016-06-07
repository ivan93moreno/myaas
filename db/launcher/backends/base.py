from abc import ABCMeta, abstractmethod, abstractproperty
from subprocess import call
import time
import os
import shutil
import socket
import logging

from .. import settings
from ..utils.container import find_container
from .exceptions import (NonExistentDatabase, NonExistentTemplate,
                         NotReachableException, DBTimeoutException,
                         ImportInProgress)


logger = logging.getLogger(__name__)


class AbstractDatabase(metaclass=ABCMeta):
    """Abstract implementation for a database backend"""

    not_found_exception_class = NonExistentDatabase

    def __init__(self, docker_client, template, name, create=False):
        self.client = docker_client
        self.template = template
        self.name = name
        self.create = create
        self.container_name = self._generate_container_name(template, name)
        self.container = find_container(self.container_name)
        if not self.container:
            self.container = self._create_container()

    @abstractproperty
    def datadir(self):
        pass

    @abstractproperty
    def image(self):
        pass

    @abstractproperty
    def environment(self):
        pass

    @abstractproperty
    def provider(self):
        pass

    @abstractproperty
    def internal_port(self):
        pass

    @abstractproperty
    def password(self):
        pass

    @property
    def container_path(self):
        return os.path.join(settings.DATA_DIR, self.container_name)

    @property
    def host_path(self):
        return os.path.join(settings.HOST_DATA_DIR, self.container_name)

    @property
    def mem_limit(self):
        return '1g'

    @property
    def internal_ip(self):
        return self.inspect()['NetworkSettings']['IPAddress']

    @property
    def external_ip(self):
        return settings.HOSTNAME

    @property
    def restart_policy(self):
        return {"MaximumRetryCount": 0, "Name": "always"}

    @property
    def external_port(self):
        port_name = '{}/tcp'.format(self.internal_port)
        ports = self.inspect()['NetworkSettings']['Ports']
        if not ports:
            return None
        elif port_name not in ports:
            return None
        return ports[port_name][0]["HostPort"]

    @property
    def user(self):
        return "root"

    @property
    def database(self):
        return "default"

    @abstractmethod
    def test_connection(self, timeout=1):
        if not self.internal_port:
            raise NotReachableException("Could not find container port")
        if not self.internal_ip:
            raise NotReachableException("Could not find container IP, is container running?")

    def start(self):
        self.client.start(self.container)

    def stop(self):
        self.client.stop(self.container)
        self.client.wait(self.container)

    def running(self):
        return self.inspect()['State']['Running']

    def destroy(self):
        if self.running():
            self.stop()
        self.client.remove_container(self.container)
        self.container = None

    def purge(self):
        if self.container:
            self.destroy()

        if self._datadir_created():
            self._delete_volume_data()

    def wait_until_active(self):
        tries = 0
        while tries < 30:
            if self.test_connection():
                return
            time.sleep(5)
            tries += 1

        raise DBTimeoutException("Could not connect with database, max retries reached")

    def inspect(self):
        data = self.client.inspect_container(self.container)
        if not data:
            raise Exception("Docker inspect data not available")
        return data

    def backup_datadir(self, move=False):
        if self.running():
            self.stop()

        backup_path = self._get_backup_path()

        # remove previous backup if exists
        self.remove_backup()

        if not os.path.isdir(self.container_path):
            return  # nothing to backup

        if move:
            call(["mv", self.container_path, backup_path])
        else:
            # reflink=auto will use copy on write if supported
            call(["cp", "-r", "--reflink=auto", self.container_path, backup_path])

    def remove_backup(self):
        backup_path = self._get_backup_path()
        if os.path.isdir(backup_path):
            call(["rm", "-rf", backup_path])

    def restore_datadir(self):
        if self.running():
            self.stop()

        backup_dir = self._get_backup_path()

        if not os.path.isdir(backup_dir):
            return False

        call(["rm", "-rf", self.container_path])
        call(["mv", backup_dir, self.container_path])
        return True

    def _get_backup_path(self):
        parent_dir, data_dir = os.path.split(self.container_path)
        data_dir = 'backup-{}'.format(data_dir)
        return os.path.join(parent_dir, data_dir)

    def _create_container(self):
        if not self.create:
            raise self.not_found_exception_class()

        return self.client.create_container(
            image=self.image,
            name=self.container_name,
            environment=self.environment,
            ports=[self.internal_port],
            volumes=[self.datadir],
            host_config=self._get_host_config_definition(),
            labels=self._get_container_labels())

    def _generate_container_name(self, template, name=None):
        if name:
            return '%s%s-%s' % (settings.CONTAINER_PREFIX, template, name)
        else:
            return '%s%s' % (settings.CONTAINER_PREFIX, template)

    def _get_volumes_definition(self):
        return [self.datadir]

    def _get_host_config_definition(self):
        "create host_config object with permanent port mapping"
        host_config = self.client.create_host_config(
            port_bindings={
                self.internal_port: self._get_free_port(),
            },
            binds={
                self.host_path: {'bind': self.datadir, 'ro': False}
            },
            mem_limit=self.mem_limit,
            restart_policy=self.restart_policy
        )
        logger.debug(host_config)

        return host_config

    def _get_port_bindings(self):
        return {self.internal_port: ('0.0.0.0',)}

    def _get_container_labels(self):
        return {
            'com.myaas.provider': self.provider,
            'com.myaas.is_template': 'False',
            'com.myaas.template': self.template,
            'com.myaas.instance': self.name,
        }

    def _datadir_created(self):
        return os.path.isdir(self.container_path)

    def _delete_volume_data(self):
        shutil.rmtree(self.container_path)

    def _get_free_port(self):
        """
        This method finds a free port number for new containers to be created,
        it releases the port just before returning the port number, so there is
        a chance for another process to get it, let's see if it works.

        This requires the myaas container to be running with --net=host otherwise
        the port returned by this method will be a free port inside the container,
        but may not be free on the host machine.
        """
        s = socket.socket()
        s.bind(("", 0))
        (ip, port) = s.getsockname()
        s.close()
        logger.debug("Assigning port {}".format(port))
        return port


class AbstractDatabaseTemplate(AbstractDatabase):
    """
    Abstract implementation of a template database
    """

    not_found_exception_class = NonExistentTemplate

    def __init__(self, docker_client, template, create=False):
        super().__init__(docker_client, template, None, create)

    @property
    def restart_policy(self):
        return {"MaximumRetryCount": 0, "Name": "no"}

    @abstractproperty
    def database_backend(self):
        pass

    @abstractmethod
    def import_data(self, sql_file):
        pass

    @abstractmethod
    def get_engine_status(self):
        pass

    def _get_container_labels(self):
        return {
            'com.myaas.is_template': 'True',
            'com.myaas.provider': self.provider,
            'com.myaas.template': self.template,
        }

    def clone(self, name):
        if self.running():
            raise ImportInProgress

        database = self.database_backend(self.client, self.template, name, create=True)

        template_data_path = os.path.join(self.container_path, '.')
        db_data_path = database.container_path
        # reflink=auto will use copy on write if supported
        call(["cp", "-r", "--reflink=auto", template_data_path, db_data_path])

        return database