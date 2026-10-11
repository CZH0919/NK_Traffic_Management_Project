from django.db import models

class GeometryField(models.Field):
    """EWKT adapter for ordinary PostgreSQL backend; no GeoDjango/GDAL dependency.

    Geometry filters and transformations use explicitly parameterized PostGIS SQL.
    No implicit degree-to-metre conversion or GIS ORM lookups are supported.
    """
    def __init__(self, *args, geometry_type='POINT', srid=4326, **kwargs):
        if geometry_type not in {'POINT', 'MULTILINESTRING', 'MULTIPOLYGON'} or srid != 4326:
            raise ValueError('Only the documented WGS84 geometry types are supported.')
        self.geometry_type, self.srid = geometry_type, srid
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.update(geometry_type=self.geometry_type, srid=self.srid)
        return name, path, args, kwargs

    def db_type(self, connection):
        return f'geometry({self.geometry_type},{self.srid})'

    def get_placeholder(self, value, compiler, connection):
        return 'ST_GeomFromEWKT(%s)'

    def select_format(self, compiler, sql, params):
        return f'ST_AsEWKT({sql})', params
