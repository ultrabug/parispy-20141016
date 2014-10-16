#/usr/bin/python
# -*- coding: utf-8 -*-

import gevent
import uwsgi

from flask import Flask
from os import environ
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from uwsgidecorators import spoolraw

# uWSGI spooling retry interval when idle
uwsgi.set_spooler_frequency(15)

# Flask app declaration
app = Flask(__name__)
app.config['MONGO_HOST'] = environ.get('MONGO_HOST', 'localhost')
app.config['MONGO_DBNAME'] = environ.get('MONGO_DBNAME', 'test')
app.last_count = 0
app.mongo = None


def get_mongo():
    """
    Simply connect the app to mongoDB.
    """
    try:
        app.mongo = MongoClient(
            host=app.config['MONGO_HOST'],
            connectTimeoutMS=500,
            socketTimeoutMS=1000
        )[app.config['MONGO_DBNAME']]
    except ConnectionFailure:
        app.mongo = None

# opportunistic initial connect
get_mongo()


def increment_counter():
    """
    Increment by 1 the counter in mongoDB and fail seamlessly.
    """
    try:
        app.mongo.counter.update(
            {'_id': 'counter'},
            {'$inc': {'value': 1}},
            upsert=True
        )
    except:
        # since our main functions use the same code, we have to separate
        # the error handling between a frontend and a spooler process
        if uwsgi.i_am_the_spooler():
            # failure means a mongoDB exception so let's try to reconnect
            get_mongo()

            # re-raise the exception so it's catched in the spooler function
            raise
        else:
            # failure means a mongoDB exception so let's try to reconnect
            gevent.spawn(get_mongo)

            # the frontend should spool a message to the spooler to ensure
            # that the increment will be retried and no data will be lost
            spooler.spool({'message': '1'})
    finally:
        app.last_count += 1


def get_counter():
    """
    Get the counter from mongoDB or use the app's counter as a fallback.
    """
    try:
        doc = app.mongo.counter.find_one({'_id': 'counter'})
        current_count = int(doc['value'])
    except:
        # something went wrong, use the app counter as a fallback
        app.mongo = None
        return app.last_count
    else:
        # set the fallback counter to the latest mongoDB value
        app.last_count = current_count
        return current_count


@app.route('/')
def show_counter():
    """
    Main frontend logic.
    """
    # increment the counter, this is non blocking and can happen anytime
    gevent.spawn(increment_counter)

    # get the current counter from mongoDB in a concurrent way but in less
    # than 2 seconds to ensure that our app is consistently fast
    counter = gevent.with_timeout(
        seconds=2,
        timeout_value=app.last_count,
        function=get_counter
    )

    # we locally add one to the latest counter to account our own call
    counter = "{:,}".format(counter + 1)

    return '<h1 style="color:{}">{}</h1><h2>mongoDB host {} is {}</h2>'.format(
        'green' if app.mongo is not None else 'orange',
        counter,
        app.config['MONGO_HOST'],
        'up :)' if app.mongo is not None else 'down ;('
    )


@spoolraw
def spooler(env):
    """
    Retry any failed attempt to increment the counter in mongoDB.
    """
    try:
        increment_counter()
    except:
        # didn't work ? we'll retry later so keep the spool file
        return uwsgi.SPOOL_RETRY
    else:
        # worked, remove the spool file
        return uwsgi.SPOOL_OK
