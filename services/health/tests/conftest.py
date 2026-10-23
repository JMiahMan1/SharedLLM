"""Fixtures for the health service tests.

Each test gets its own SQLite file and a TestClient pointed at it. The app's
lifespan also creates the tables, but the fixture does it first so a test can
seed rows before the first request.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from services.health import main as health_main


def write_mini_seed(root: Path) -> Path:
    """Build a tiny USDA seed from fixture files (no network, no downloads)."""
    from scripts.build_fdc_seed import build_seed

    foundation = root / "foundation"
    sr = root / "sr_legacy"
    foundation.mkdir()
    sr.mkdir()

    def write(path: Path, header: str, rows: list[str]) -> None:
        path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    write(foundation / "foundation_food.csv", '"fdc_id","NDB_number","footnote"', ['"111","1111",""', '"222","",""'])
    write(foundation / "food_category.csv", '"id","description"', ['"11","Vegetables"'])
    write(
        foundation / "food.csv",
        '"fdc_id","data_type","description","food_category_id","publication_date"',
        [
            '"111","foundation_food","Broccoli, raw","11","2026-04-30"',
            '"222","foundation_food","Beans, snap, green, raw","11","2026-04-30"',
            '"999","sample_food","Not imported","11","2026-04-30"',
        ],
    )
    write(
        foundation / "food_nutrient.csv",
        '"id","fdc_id","nutrient_id","amount"',
        [
            '"1","111","1008","34"',
            '"2","111","1003","2.8"',
            '"3","111","1063","3.3"',
            '"4","111","2000","3.0"',
            '"5","999","1008","999"',
        ],
    )
    write(foundation / "measure_unit.csv", '"id","name"', ['"1000","cup"', '"9999","undetermined"'])
    write(
        foundation / "food_portion.csv",
        '"id","fdc_id","seq_num","amount","measure_unit_id","portion_description","modifier","gram_weight"',
        [
            '"1","111","1","1.0","1000","chopped","","36.0"',
            '"2","111","2","1","9999","","serving","21.0"',
            '"3","111","3","1","9999","","serving","21.0"',
            '"4","111","4","1","9999","","","0"',
        ],
    )

    write(sr / "sr_legacy_food.csv", '"fdc_id","NDB_number"', ['"333","3333"'])
    write(sr / "food_category.csv", '"id","description"', ['"20","Cereal Grains and Pasta"'])
    write(
        sr / "food.csv",
        '"fdc_id","data_type","description","food_category_id","publication_date"',
        ['"333","sr_legacy_food","Rice, white, cooked","20","2018-04"'],
    )
    write(sr / "food_nutrient.csv", '"id","fdc_id","nutrient_id","amount"', ['"1","333","1008","130"', '"2","333","1005","28.2"'])
    write(sr / "measure_unit.csv", '"id","name"', ['"1000","cup"', '"9999","undetermined"'])
    sr_portions = ['"1","333","1","1","1000","","cooked","158.0"']
    for index in range(1, 14):
        sr_portions.append(f'"{index + 1}","333","{index + 1}","1","9999","","item {index}","{index * 10}.0"')
    write(
        sr / "food_portion.csv",
        '"id","fdc_id","seq_num","amount","measure_unit_id","portion_description","modifier","gram_weight"',
        sr_portions,
    )

    fndds = {
        "SurveyFoods": [
            {
                "fdcId": 444444,
                "foodCode": "56205190",
                "description": "Rice, white, cooked",
                "wweiaFoodCategory": {"wweiaFoodCategoryDescription": "Rice"},
                "foodNutrients": [
                    {"nutrient": {"id": 1008, "number": "208"}, "amount": 97.0},
                    {"nutrient": {"id": 1003, "number": "203"}, "amount": 2.0},
                    {"nutrient": {"id": 9999, "number": "999"}, "amount": 1.0},
                    {"nutrient": {"id": 1005, "number": "205"}},
                ],
                "foodPortions": [
                    {"gramWeight": 174, "portionDescription": "1 cup, cooked", "sequenceNumber": 1},
                    {"gramWeight": 0, "portionDescription": "Quantity not specified", "sequenceNumber": 2},
                    {"gramWeight": 24, "portionDescription": "", "measureUnit": {"name": "undetermined"}, "sequenceNumber": 3},
                ],
            }
        ]
    }
    fndds_path = root / "surveyDownload.json"
    fndds_path.write_text(json.dumps(fndds), encoding="utf-8")

    out = root / "fdc-seed.db"
    build_seed(out, foundation, sr, fndds_path, expect={"foundation": 2, "sr_legacy": 1, "fndds": 1})
    return out


@pytest.fixture
def mini_seed_path(tmp_path) -> Path:
    return write_mini_seed(tmp_path)


@pytest.fixture
def engine(tmp_path):
    created = create_engine(
        f"sqlite:///{tmp_path / 'health.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(created)
    return created


@pytest.fixture
def client(engine):
    health_main.app.state.engine = engine
    with TestClient(health_main.app) as test_client:
        test_client.headers.update(
            {
                "X-Internal-Secret": health_main.INTERNAL_SECRET,
                "X-User-Id": "jeremiah",
            }
        )
        yield test_client
    health_main.app.state.engine = None
