# MyASS (Mysql As A Service)

This product has been developed internally at habitissimo for allowing developers to get the database instances they need for development as fast as possible.

## What this project does

This project consists on a service which will import a collection of databases periodically. This databases become templates for the final users.

An user can ask for a database instance from any template available and have it fully functional and loaded with data within seconds, no matter how big is the database, this databases can be destroyed at any moment to request a new instance with fresh data.

## Speed

Tha main concern we where having in our development process was importing database backups in our development instances, loading this backups by tradicional means (importing a mysqldump file) could take almost an hour, we could use other metohds like innobackupex, but this would mean developers had to download huge files (even with compression) trading speed in import time by slownes in download time.

This solution is being used to provide a variety of databases ranging from a few megabytes up to several gigabytes, all of them are provisioned within seconds (something between 3 or 5 seconds).

## How it works

You put your sql backups in a folder and run the updater command, this will import the databases and prepare them as templates. This is the slow part, we run it at nights so developers can have acces to yesterday's data in the morning.

Once the templates have been loaded the script stops the template database instances.

Every time a user asks for a new database the service performs a copy on write from the template to a new directory, this directory is mounted as a volume
for a new mysql docker instance launched for this user.

Finally the service responds with access data required to use the database.

## What you will find here:

 - **db**: databases as a service [read more](db/README.md)
 - **fabfile**: example client to interact with myass [read more](fabfile/README.md)

## TODO
 - [ ] Use docker volume API instead of hacking arround with volume bindings
 - [ ] Create adapters for postgresql and mongodb

## Support

If you have problems using this service [open an issue](../../Habitissimo/myass/issues).
