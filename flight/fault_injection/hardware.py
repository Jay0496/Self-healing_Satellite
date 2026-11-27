"""Direct I2C register access for physical satellite sensors."""

from contextlib import contextmanager

I2C_BUS = 1

INA219_ADDRESS = 0x40
MPU6050_ADDRESS = 0x68
BMP280_ADDRESS = 0x77

SENSOR_ADDRESSES = {
    "ina219": INA219_ADDRESS,
    "mpu6050": MPU6050_ADDRESS,
    "bmp280": BMP280_ADDRESS,
}


@contextmanager
def open_bus():
    """Open and safely close the Raspberry Pi I2C bus."""
    from smbus2 import SMBus

    with SMBus(I2C_BUS) as bus:
        yield bus


def read_register(bus, sensor, register):
    """Read an actual hardware register."""
    address = SENSOR_ADDRESSES[sensor]

    if sensor == "ina219":
        data = bus.read_i2c_block_data(address, register, 2)
        return (data[0] << 8) | data[1]

    return bus.read_byte_data(address, register)


def write_register(bus, sensor, register, value):
    """Write directly to an actual hardware register."""
    address = SENSOR_ADDRESSES[sensor]

    if sensor == "ina219":
        if not 0 <= value <= 0xFFFF:
            raise ValueError("Invalid 16-bit register value")

        bus.write_i2c_block_data(
            address,
            register,
            [(value >> 8) & 0xFF, value & 0xFF],
        )
    else:
        if not 0 <= value <= 0xFF:
            raise ValueError("Invalid 8-bit register value")

        bus.write_byte_data(address, register, value)


def verify_register(bus, sensor, register, expected):
    """Verify that the hardware accepted the register change."""
    actual = read_register(bus, sensor, register)

    if actual != expected:
        raise RuntimeError(
            f"{sensor}: register 0x{register:02X} "
            f"expected 0x{expected:X}, received 0x{actual:X}"
        )

    return actual
