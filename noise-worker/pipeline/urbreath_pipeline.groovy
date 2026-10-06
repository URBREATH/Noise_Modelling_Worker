/**
 * URBREATH - Lden noise pipeline for a single job.
 * Usage: wps_scripts -w <jobDir> -s urbreath_pipeline.groovy -jobDir <jobDir>
 */

import org.slf4j.Logger
import org.slf4j.LoggerFactory

import java.sql.Connection
import groovy.sql.Sql

title = 'URBREATH noise pipeline'
description = 'area.osm.pbf + polygon -> Lden isocontours as GeoJSON'

inputs = [
        jobDir: [
                name       : 'Job folder',
                title      : 'Job folder',
                description: 'Job folder containing area.osm.pbf and input_polygon.geojson',
                type       : String.class
        ]
]

// Optional physical parameters for "custom" mode. Omitted = NoiseModelling default.
// Declared as String: the worker validates them, the conversion is done in exec().
CUSTOM_PARAMS = ['confMaxSrcDist', 'confMaxReflDist', 'confReflOrder', 'paramWallAlpha',
                 'confDiffVertical', 'confDiffHorizontal', 'confHumidity', 'confTemperature']
CUSTOM_PARAMS.each { key ->
    inputs[key] = [name: key, title: key, description: 'Optional NoiseModelling parameter',
                   min: 0, max: 1, type: String.class]
}


outputs = [
        result: [
                name       : 'Result',
                title      : 'Result',
                description: 'Final message',
                type       : String.class
        ]
]

def runScript(Connection connection, String scriptPath, Map arguments) {
    Logger logger = LoggerFactory.getLogger("urbreath")
    logger.info("Running " + scriptPath)
    Script script = new GroovyShell().parse(new File(scriptPath))
    Object result = script.invokeMethod("exec", [connection, arguments] as Object[])
    if (result != null) {
        logger.info(result.toString())
    }
}

def exec(Connection connection, Map input) {
    Logger logger = LoggerFactory.getLogger("urbreath")

    File jobDir = new File(input['jobDir'] as String)
    File pbf = new File(jobDir, 'area.osm.pbf')
    File polygon = new File(jobDir, 'input_polygon.geojson')

    [pbf, polygon].each { f ->
        if (!f.isFile()) {
            throw new IllegalArgumentException("Missing file: " + f.absolutePath)
        }
    }

        logger.info("Job: " + jobDir.absolutePath)

    // Step 3: import buildings, roads and ground from the PBF, projected to EPSG:3035
    runScript(connection, "noisemodelling/wps/Import_and_Export/Import_OSM.groovy",
            [pathFile: pbf.absolutePath, targetSRID: 3035])

    Sql sql = new Sql(connection)
    
    def logCounts = { List<String> tables ->
        tables.each { table ->
            int rows = sql.firstRow("SELECT COUNT(*) AS N FROM " + table).N as int
            logger.info(table + ": " + rows + " rows")
        }
    }
    logCounts(['BUILDINGS', 'ROADS', 'GROUND'])

        // Step 4: Delaunay receivers grid, limited to the original (unbuffered) polygon
    String fenceWkt = sql.firstRow(
            "SELECT ST_AsText(ST_GeomFromGeoJSON(?)) AS WKT", [polygon.text]).WKT as String
    logger.info("Fence (WGS84): " + fenceWkt)

    runScript(connection, "noisemodelling/wps/Receivers/Delaunay_Grid.groovy",
            [tableBuilding: 'BUILDINGS', sourcesTableName: 'ROADS', fence: fenceWkt])
    logCounts(['RECEIVERS', 'TRIANGLES'])

        // Step 5: road emission + propagation (CNOSSOS-EU); custom physical parameters if given
    def nlftParams = [tableBuilding : 'BUILDINGS',
                      tableRoads    : 'ROADS',
                      tableReceivers: 'RECEIVERS',
                      tableGroundAbs: 'GROUND']
    CUSTOM_PARAMS.each { key ->
        if (input[key] != null) {
            String value = input[key] as String
            nlftParams[key] = key.startsWith('confDiff') ? Boolean.valueOf(value) : value
            logger.info("Custom parameter " + key + " = " + value)
        }
    }
    runScript(connection, "noisemodelling/wps/NoiseModelling/Noise_level_from_traffic.groovy", nlftParams)

    sql.eachRow("SELECT PERIOD, COUNT(*) AS N, ROUND(MIN(LAEQ), 1) AS MIN_DB, ROUND(MAX(LAEQ), 1) AS MAX_DB " +
            "FROM RECEIVERS_LEVEL GROUP BY PERIOD ORDER BY PERIOD") { row ->
        logger.info("RECEIVERS_LEVEL " + row.PERIOD + ": " + row.N + " rows, LAEQ " +
                row.MIN_DB + " to " + row.MAX_DB + " dB")
    }

        // Step 6: keep only the Lden period, then build the isosurfaces from it
    sql.execute("DROP TABLE IF EXISTS RECEIVERS_LEVEL_DEN")
    sql.execute("CREATE TABLE RECEIVERS_LEVEL_DEN AS SELECT * FROM RECEIVERS_LEVEL WHERE PERIOD = 'DEN'")
    sql.execute("CREATE INDEX ON RECEIVERS_LEVEL_DEN(IDRECEIVER)")

    runScript(connection, "noisemodelling/wps/Acoustic_Tools/Create_Isosurface.groovy",
            [resultTable: 'RECEIVERS_LEVEL_DEN'])

    sql.eachRow("SELECT ISOLVL, ISOLABEL, COUNT(*) AS N, ROUND(SUM(ST_Area(THE_GEOM))) AS AREA_M2 " +
            "FROM CONTOURING_NOISE_MAP GROUP BY ISOLVL, ISOLABEL ORDER BY ISOLVL") { row ->
        logger.info("Class " + row.ISOLABEL + " dB: " + row.N + " polygons, " + row.AREA_M2 + " m2")
    }

        // Step 7: clip the isosurfaces to the original polygon and merge them into one geometry per class
    sql.execute("DROP TABLE IF EXISTS FENCE")
    sql.execute("CREATE TABLE FENCE(THE_GEOM GEOMETRY(POLYGON, 3035))")
    sql.execute("INSERT INTO FENCE VALUES (ST_Transform(ST_GeomFromText(?, 4326), 3035))", [fenceWkt])

    sql.execute("DROP TABLE IF EXISTS CONTOURING_LDEN")
    sql.execute("""
        CREATE TABLE CONTOURING_LDEN AS
        SELECT c.ISOLVL, c.ISOLABEL,
                ST_CollectionExtract(ST_Union(ST_Accum(ST_Intersection(ST_MakeValid(c.THE_GEOM), f.THE_GEOM))), 3) AS THE_GEOM
        FROM CONTOURING_NOISE_MAP c, FENCE f
        WHERE ST_Intersects(c.THE_GEOM, f.THE_GEOM)
        GROUP BY c.ISOLVL, c.ISOLABEL
    """)

    logger.info("Fence area: " + Math.round(sql.firstRow("SELECT ST_Area(THE_GEOM) AS A FROM FENCE").A as double) + " m2")
    sql.eachRow("SELECT ISOLABEL, ROUND(ST_Area(THE_GEOM)) AS AREA_M2 FROM CONTOURING_LDEN ORDER BY ISOLVL") { row ->
        logger.info("Clipped class " + row.ISOLABEL + " dB: " + row.AREA_M2 + " m2")
    }

        // Step 8: reproject to WGS84 (GeoJSON standard, RFC 7946) and export
    sql.execute("DROP TABLE IF EXISTS CONTOURING_LDEN_WGS84")
    sql.execute("""
        CREATE TABLE CONTOURING_LDEN_WGS84 AS
        SELECT ISOLVL, ISOLABEL, ST_Transform(THE_GEOM, 4326) AS THE_GEOM
        FROM CONTOURING_LDEN
        ORDER BY ISOLVL
    """)

    File output = new File(jobDir, 'lden_isocontours.geojson')
    runScript(connection, "noisemodelling/wps/Import_and_Export/Export_Table.groovy",
            [exportPath: output.absolutePath, tableToExport: 'CONTOURING_LDEN_WGS84'])
    logger.info("Output: " + output.absolutePath + " (" + output.length() + " bytes)")

    return "Pipeline OK"
}