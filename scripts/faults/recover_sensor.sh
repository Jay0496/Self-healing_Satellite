#!/bin/bash
# Restore default I2C permissions (usually owned by root, group i2c)
sudo chmod 660 /dev/i2c-1
sudo chown root:i2c /dev/i2c-1
echo "I2C bus restored. test.py can read normally."