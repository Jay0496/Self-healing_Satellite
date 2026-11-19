import csv
from datetime import datetime, timezone

import pytest

from shared.protocol import TelemetryRecord, TelemetryValidationError
from simulator.generation import (
    GeneratorConfig,
    TelemetryGenerator,
    load_uhf_templates,
)
from simulator.pipeline import IdentityTransform, select_records, transform_frame


def write_uhf_csv(path, rows):
    fields = [
        "Timestamp",
        "NanoMind_Temp",
        "Battery_Voltage",
        "X_Coarse_Spin",
        "Y_Coarse_Spin",
        "Z_Coarse_Spin",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def sample_uhf_csv(tmp_path):
    path = tmp_path / "uhf.csv"
    write_uhf_csv(
        path,
        [
            {
                "Timestamp": "2024-05-01T00:00:10Z",
                "NanoMind_Temp": "200",
                "Battery_Voltage": "7000",
                "X_Coarse_Spin": "-1",
                "Y_Coarse_Spin": "0",
                "Z_Coarse_Spin": "1",
            },
            {
                "Timestamp": "2024-05-01T00:00:00Z",
                "NanoMind_Temp": "100",
                "Battery_Voltage": "8000",
                "X_Coarse_Spin": "1",
                "Y_Coarse_Spin": "2",
                "Z_Coarse_Spin": "3",
            },
            {
                "Timestamp": "2024-05-01T00:00:10Z",
                "NanoMind_Temp": "300",
                "Battery_Voltage": "9000",
                "X_Coarse_Spin": "3",
                "Y_Coarse_Spin": "4",
                "Z_Coarse_Spin": "5",
            },
            {
                "Timestamp": "2024-05-01T00:20:00Z",
                "NanoMind_Temp": "400",
                "Battery_Voltage": "10000",
                "X_Coarse_Spin": "5",
                "Y_Coarse_Spin": "6",
                "Z_Coarse_Spin": "7",
            },
        ],
    )
    return path


def test_template_loader_sorts_and_aggregates_duplicate_timestamps(sample_uhf_csv):
    templates = load_uhf_templates(sample_uhf_csv)

    temperature = templates["NanoMind_Temp"]
    assert temperature.timestamps == tuple(sorted(temperature.timestamps))
    assert temperature.values == (100.0, 250.0, 400.0)
    assert temperature.sample_at(
        datetime(2024, 5, 1, 0, 0, 5, tzinfo=timezone.utc), max_gap_seconds=30
    ) == 175.0
    assert temperature.sample_at(
        datetime(2024, 5, 1, 0, 5, tzinfo=timezone.utc), max_gap_seconds=30
    ) is None


def test_generator_is_reproducible_and_emits_synchronized_sensor_records(sample_uhf_csv):
    config = GeneratorConfig(seed=17, run_id="repeatable", sample_rate_hz=2.0)
    left = TelemetryGenerator(sample_uhf_csv, config)
    right = TelemetryGenerator(sample_uhf_csv, config)

    left_frames = list(left.frames(3))
    right_frames = list(right.frames(3))

    assert [[record.to_json() for record in f.records] for f in left_frames] == [
        [record.to_json() for record in f.records] for f in right_frames
    ]
    assert len(left_frames[0].records) == 14
    assert all(record.measurement_time == left_frames[0].measurement_time for record in left_frames[0].records)
    assert left_frames[1].measurement_time == "2024-05-01T00:00:00.500Z"
    assert left_frames[0].records[0].sensor_id == "bmp0"
    assert left_frames[0].records[0].provenance == "generated_mock"
    assert left_frames[0].records[0].source.startswith("ops_sat_uhf_template:")


def test_generated_fields_are_bounded_and_derived_power_is_consistent(sample_uhf_csv):
    generator = TelemetryGenerator(sample_uhf_csv, GeneratorConfig(seed=3))
    for frame in generator.frames(30):
        values = {record.signal: record for record in frame.records}
        assert 15.0 <= values["temperature_c"].value <= 35.0
        assert 950.0 <= values["pressure_hpa"].value <= 1050.0
        assert 7.0 <= values["bus_voltage_v"].value <= 8.2
        assert 0.0 <= values["current_ma"].value <= 500.0
        assert values["power_mw"].value == pytest.approx(
            values["bus_voltage_v"].value * values["current_ma"].value,
            abs=0.002,
        )
        assert values["power_mw"].provenance == "derived"
        assert all(-2.0 <= values[f"gyro_{axis}_dps"].value <= 2.0 for axis in "xyz")


def test_missing_template_regions_use_synthetic_fallback(sample_uhf_csv):
    generator = TelemetryGenerator(
        sample_uhf_csv,
        GeneratorConfig(seed=1, template_max_gap_seconds=30.0),
    )
    frames = list(generator.frames(12))
    temperature = {record.signal: record for record in frames[11].records}["temperature_c"]

    assert temperature.source == "synthetic_model"
    assert temperature.transform_id == "temperature_fallback_v1"
    assert temperature.provenance == "generated_mock"


def test_identity_transform_preserves_records(sample_uhf_csv):
    frame = TelemetryGenerator(sample_uhf_csv).next_frame()
    transformed = transform_frame(frame, IdentityTransform())

    assert transformed == frame


def test_signal_selection_supports_selected_channel_throughput_tests(sample_uhf_csv):
    frame = TelemetryGenerator(sample_uhf_csv).next_frame()
    transformed = transform_frame(frame, IdentityTransform())

    selected = select_records(transformed, {"bus_voltage_v"})
    assert len(selected) == 1
    assert selected[0].signal == "bus_voltage_v"
    with pytest.raises(ValueError, match="unknown simulator signals"):
        select_records(transformed, {"unknown_channel"})


def test_transform_cannot_change_sample_identity_or_timestamps(sample_uhf_csv):
    class InvalidTransform:
        def apply(self, record):
            return TelemetryRecord(
                **{
                    **record.to_dict(),
                    "measurement_time": "2024-05-02T00:00:00Z",
                }
            )

    frame = TelemetryGenerator(sample_uhf_csv).next_frame()
    with pytest.raises(ValueError, match="preserve measurement_time"):
        transform_frame(frame, InvalidTransform())


def test_transform_output_must_preserve_derived_power_invariant(sample_uhf_csv):
    class InvalidTransform:
        def apply(self, record):
            if record.signal == "bus_voltage_v":
                return TelemetryRecord(**{**record.to_dict(), "value": record.value + 0.5})
            return record

    frame = TelemetryGenerator(sample_uhf_csv).next_frame()
    with pytest.raises(ValueError, match="power_mw must equal"):
        transform_frame(frame, InvalidTransform())


def test_record_contract_rejects_timezone_free_and_nonfinite_values(sample_uhf_csv):
    record = TelemetryGenerator(sample_uhf_csv).next_frame().records[0]
    with pytest.raises(TelemetryValidationError, match="include a timezone"):
        TelemetryRecord(**{**record.to_dict(), "measurement_time": "2024-05-01T00:00:00"}).validate()
    with pytest.raises(TelemetryValidationError, match="finite"):
        TelemetryRecord(**{**record.to_dict(), "value": float("nan")}).validate()
