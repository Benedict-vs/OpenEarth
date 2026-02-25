import ee
ee.Authenticate()
ee.Initialize(project='openearth-488015')

no2 = (ee.ImageCollection("COPERNICUS/S5P/OFFL/L3_NO2")
         .select('tropospheric_NO2_column_number_density')
         .filterDate('2026-02-01', '2026-02-10'))

print(no2)
