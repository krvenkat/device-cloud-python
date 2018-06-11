#!/usr/bin/env python
import errno
import json
import os
from os.path import abspath
import platform
import signal
import sys
from time import sleep
import uuid 
import argparse
import tarfile 
import random
import socket
import math
from datetime import datetime, timedelta
from device_cloud import osal
from device_cloud import ota_handler
from device_cloud import relay
import device_cloud as iot
from device_cloud import sds
if sys.version_info.major == 2:
    input = raw_input

# set the path so that we find the device_cloud dir one level up first
head, tail = os.path.split(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, head)

running = True
sending_telemetry = True

# Return status once the cloud responds
cloud_response = False

# Second intervals between telemetry
TELEMINTERVAL = 4

def sighandler(signum, frame):
    """
    Signal handler for exiting app
    """
    global running
    if signum == signal.SIGINT:
        print("Received SIGINT, stopping application...")
        running = False

if __name__ == "__main__":
    signal.signal(signal.SIGINT, sighandler)

    # Parse command line arguments for easy customization
    parser = argparse.ArgumentParser(description="Demo app for Python HDC "
                                     "telemetry APIs")
    parser.add_argument("-i", "--app_id", help="Custom app id")
    parser.add_argument("-c", "--config_dir", help="Custom config directory")
    parser.add_argument("-f", "--config_file", help="Custom config file name "
                        "(in config directory)")
    args = parser.parse_args(sys.argv[1:])

    # Initialize client default called 'python-demo-app'
    app_id = "iot-sds-location-py"
    if args.app_id:
        app_id = args.app_id
    client = iot.Client(app_id)

    # Use the demo-connect.cfg file inside the config directory
    # (Default would be python-demo-app-connect.cfg)
    config_file = "demo-iot-sds.cfg"
    if args.config_file:
        config_file = args.config_file
    client.config.config_file = config_file

    # Look for device_id and demo-connect.cfg in this directory
    # (This is already default behaviour)
    config_dir = "."
    if args.config_dir:
        config_dir = args.config_dir
    client.config.config_dir = config_dir

    # Finish configuration and initialize client
    client.initialize()

    plus_minus = 25
    print("""
    This demo uses SDS to store location data and does the following:
        * Store N Forward
        * use case: keep data on device for x number of days
        * smart publishing: data smoothing to send on significant change  (+/- {})
        * show data aging work 
    """.format(plus_minus))
    input(" Hit Enter to proceed to demo...")

    # ------------------------------------------
    # initialize the data store 
    # ------------------------------------------
    db_name = app_id + ".db"

    # for demo purposes, remove the db before hand
    if os.path.exists(db_name):
        os.remove(db_name)
    client.sds = sds.SmartDataStorage( app_id +".db", logger=client.handler.logger)

    # ------------------------------------------
    # configure the data storage policy
    # ------------------------------------------
    # Retention modes:
    #   STORAGE_MAX_DAYS
    #   Keep telemetry until the time stamps are greather than
    #   max_days_to_retain, then delete those that are older
    client.sds.retention_policy   = client.sds.STORAGE_MAX_DAYS
    client.sds.max_days_to_retain = 1

    # Connect to Cloud
    if client.connect(timeout=10) != iot.STATUS_SUCCESS:
        client.error("Failed")
        sys.exit(1)


    counter = 0

    # Initial location data.
    heading = random.uniform(0, 360)
    speed = 10.0
    pos_lat = 45.351603
    pos_long = -75.918713

    input("\nWe are connected now.  Collect telmetry but don't publish yet")
    while running and client.is_alive():
        if counter < 10:
            # Randomly generate location data
            speed = round(random.uniform(0, 100), 2)
            heading += round(random.random() * 20, 2) - 10
            if heading > 360:
                heading -= 360
            if heading < 0:
                heading += 360
            pos_lat += round((speed * math.cos(math.radians(heading))) \
                              * 0.001, 2)
            pos_long += round((speed * math.sin(math.radians(heading))) \
                               * 0.001, 2)
            if pos_lat not in range(-90, 90) :
                pos_lat = random.uniform(-90, 90)
            if pos_long not in range(-180, 180) :
                pos_long = random.uniform(-180, 180)

            ts = datetime.utcnow()

            print("lat {} lng {} hed {} spd {}".format(pos_lat,pos_long, heading, speed))
            status = client.sds.insert_location(
                               latitude=pos_lat,
                               longitude=pos_long,
                               heading=heading,
                               speed=speed,
                               state=client.sds.STATE_UNSENT)
            if status is not iot.STATUS_SUCCESS:
                client.handler.logger.error("Error: inserting into data store")
        else:
            running = False
        counter += 1

    input("\nHit enter to show the telemetry stored in the db...")
    client.sds.print_table(client.sds.LOCATION)

    # ------------------------------------------------------------------
    # on change detection with +/-
    # ------------------------------------------------------------------
    input("\nHit enter to smooth data so that we ignore non significant change (+/- {})...".format(plus_minus))
    client.handler.logger.info("Checking values for histeresis ...")
    ignore_list = client.sds.data_smooth_on_change( client.sds.LOCATION,
                                                    field_name='speed',
                                                    plus_minus=plus_minus, 
                                                    state=client.sds.STATE_IGNORE)
    if len(ignore_list):
        client.handler.logger.info("Marked {} items as histeresis".format(len(ignore_list)))

    input("\nHit enter to show the updated telemetry...")
    client.sds.print_table(client.sds.LOCATION)

    input("\nShow how data is aged.  This demo uses a retain period of\n"
          "1 day. Update the date field in last entry to\n"
          "a date outside the max configured retention time e.g. 3 days old ...")
   
    # update the time in the last sample to 3 days ago
    # Use the raw sql cursor for custom sql commands
    c = client.sds.get_cursor()
    sql = "select * from {} order by idx desc limit 1;".format(client.sds.LOCATION)
    c.execute(sql)
    for rows in c:
       print rows
    old_ts =  datetime.utcnow() - timedelta(days=3)
    ts = old_ts.strftime("%Y-%m-%d %H:%M:%S.%f")
    sql = "UPDATE {} SET ts =\'{}\',msg=\'X\' WHERE idx = {}".format(client.sds.LOCATION, ts, rows[0])
    print(sql)
    client.sds.custom_sql_command(sql)

    input("\nHit enter to show the updated telemetry (note last entry)...")
    client.sds.print_table(client.sds.LOCATION)

    input("\nData retention is calculated at INSERT time.\n"
          "Add a new sample to trigger the process")
    status = client.sds.insert_location(
                               latitude=pos_lat,
                               longitude=pos_long,
                               heading=heading,
                               speed=speed,
                               state=client.sds.STATE_UNSENT)

    client.sds.print_table(client.sds.LOCATION)

    input("\nHit enter publish all unsent telemetry...")

    # ------------------------------------------------------------------
    # publish anything that is "unsent" and update state to sent
    # ------------------------------------------------------------------
    client.handler.logger.info("Publishing significant telemetry...")
    status = client.sds.publish_unsent_location(client,
                             new_state=client.sds.STATE_SENT)
                             ##new_state=client.sds.STATE_REMOVE)
    if status != iot.STATUS_SUCCESS:
        client.handler.logger.error("Error: failed to pulbish some telemetry")

    # ------------------------------------------------------------------
    # dump the table
    # ------------------------------------------------------------------
    input("\nTelemetry sent, hit enter to show the states...")
    client.sds.print_table(client.sds.LOCATION)
    
    client.disconnect(wait_for_replies=True)
