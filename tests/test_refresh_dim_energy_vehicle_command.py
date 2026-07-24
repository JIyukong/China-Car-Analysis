from __future__ import annotations

import unittest

from pipelines.dim.refresh_dim_energy_vehicle import (
    DEFAULT_CSV_PATH,
    read_dim_energy_vehicle_csv,
)


class RefreshDimEnergyVehicleCommandTest(unittest.TestCase):
    def test_read_dim_energy_vehicle_csv_should_return_rows(self) -> None:
        rows = read_dim_energy_vehicle_csv(DEFAULT_CSV_PATH)
        self.assertGreater(len(rows), 0)
        self.assertIn("model_name", rows[0])
        self.assertIn("energy_type", rows[0])
        self.assertIn("vehicle_type", rows[0])


if __name__ == "__main__":
    unittest.main()

