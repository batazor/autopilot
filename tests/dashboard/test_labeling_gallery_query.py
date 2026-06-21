from dashboard.labeling_gallery_query import open_in_labeling_query_params


def test_open_in_labeling_auto_and_default() -> None:
    assert open_in_labeling_query_params("main_city.png", "auto") == {
        "ref": "main_city.png",
        "module": "core",
        "version": "default",
    }
    assert open_in_labeling_query_params("x.png", "default") == {
        "ref": "x.png",
        "module": "core",
        "version": "default",
    }


def test_open_in_labeling_force_version() -> None:
    assert open_in_labeling_query_params("main_city.png", "v2") == {
        "ref": "main_city.png",
        "module": "core",
        "version": "v2",
    }
    assert open_in_labeling_query_params("main_city.png", "V2") == {
        "ref": "main_city.png",
        "module": "core",
        "version": "v2",
    }


def test_open_in_labeling_bad_version_key_falls_back_to_default() -> None:
    assert open_in_labeling_query_params("main_city.png", "not_a_version") == {
        "ref": "main_city.png",
        "module": "core",
        "version": "default",
    }
