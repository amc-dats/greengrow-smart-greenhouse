#!/bin/sh
# Builds the broker's password and ACL files at start-up from environment variables,
# so no credential is stored in the repository, then hands over to mosquitto.
set -eu

PASSWD=/tmp/passwd
mosquitto_passwd -b -c "$PASSWD" edge      "$MQTT_EDGE_PASSWORD"
mosquitto_passwd -b    "$PASSWD" actuators "$MQTT_ACTUATORS_PASSWORD"
mosquitto_passwd -b    "$PASSWD" backend   "$MQTT_BACKEND_PASSWORD"

cp /mosquitto/acl.src /tmp/acl
cp /mosquitto/mosquitto.conf.src /tmp/mosquitto.conf
chown mosquitto:mosquitto "$PASSWD" /tmp/acl
chmod 0600 "$PASSWD" /tmp/acl

exec /usr/sbin/mosquitto -c /tmp/mosquitto.conf
