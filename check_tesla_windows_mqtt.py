#!/usr/bin/env python3

import teslapy
import paho.mqtt.client as mqtt
import json
import time
from datetime import datetime

raining = False

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("acurite/loop")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
#    print(msg.topic+" "+str(msg.payload.decode('utf-8')))
    jsondata = json.loads(str(msg.payload.decode('utf-8')))
    rain_cm = jsondata.get('rain_cm')

    if rain_cm is not None:
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
        print(current_time + ": " + rain_cm)

        rain = float(rain_cm)
        if rain > 0.0:
            if raining == False:
                raining = True
                print("Reading car")
                tesla = teslapy.Tesla('username@example.com')
                if not tesla.authorized:
                    tesla.refresh_token(refresh_token=input('Enter SSO refresh token: '))
                vehicles = tesla.vehicle_list()
                vehicles[0].sync_wake_up()  # We need to get up to date data so no choice but to wake it
                vehicleData = vehicles[0].get_vehicle_data()

                fd_window = int(vehicleData['vehicle_state']['fd_window'])
                fp_window = int(vehicleData['vehicle_state']['fp_window'])
                rd_window = int(vehicleData['vehicle_state']['rd_window'])
                rp_window = int(vehicleData['vehicle_state']['rp_window'])
                windows = fd_window + fp_window + rd_window + rp_window
                print('Number of windows open: ' + str(windows))

                moving = vehicleData['drive_state']['shift_state']
                print('Driving: ' + str(moving))

                if moving is None and windows > 0:
                    print("Closing windows!")

                    latitude=vehicleData['drive_state']['latitude']
                    longitude=vehicleData['drive_state']['longitude']

                    print(latitude)
                    print(longitude)
                    vehicles[0].command('WINDOW_CONTROL', command='close', lat=latitude, lon=longitude)
                else:
                    print("It's raining but our windows are closed")

                tesla.close()
            else:
                print("Already checked for this rain period, skipping")
        else:
            raining = False
            print("All is fine")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.username_pw_set(username="username", password="password")

print("Connecting...")
client.connect("hostname", 1883, 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()
