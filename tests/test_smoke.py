import geopandas
import osmnx
import shapely


def test_geo_stack_imports():
    assert osmnx.__version__.startswith("2.")
    assert geopandas.__version__
    assert shapely.__version__
