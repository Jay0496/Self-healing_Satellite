#!/bin/bash
# Revoke all read/write access to the I2C bus
sudo chmod 000 /dev/i2c-1
echo "I2C bus disabled. test.py will now crash on read."