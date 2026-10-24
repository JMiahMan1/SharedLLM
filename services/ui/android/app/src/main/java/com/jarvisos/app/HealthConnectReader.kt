package com.jarvisos.app

import android.content.Context
import android.os.Build
import androidx.activity.result.contract.ActivityResultContract
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.AggregateRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant
import kotlin.reflect.KClass
import kotlinx.coroutines.runBlocking
import org.json.JSONObject

/**
 * The one place that talks to Health Connect, whose client is a Kotlin
 * coroutine API. Everything here blocks (runBlocking) and is called from
 * Java on a background thread: [HealthConnectSync], the worker and the plugin.
 *
 * It only reads, and only the record types the person granted. The plain
 * fields of each record go through [HealthConnectMapping], which owns every
 * rule about what the server receives. Steps are never read: the phone's
 * pedometer already posts them.
 */
object HealthConnectReader {
    const val PROVIDER = "com.google.android.apps.healthdata"

    /** One sync's reading, as ingestion payloads, plus what could not be read. */
    class Batch {
        @JvmField val sleeps: MutableList<JSONObject> = mutableListOf()
        @JvmField val metrics: MutableList<JSONObject> = mutableListOf()
        @JvmField val workouts: MutableList<JSONObject> = mutableListOf()
        /** Exercise sessions of a kind geo cannot name (swims, strength, ...). */
        @JvmField var otherWorkouts: Int = 0
        @JvmField val errors: MutableList<String> = mutableListOf()
    }

    /** Health Connect runs on Android 9+ (an app before 14, built in from 14). */
    @JvmStatic
    fun supported(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P

    /** HealthConnectClient.SDK_* (1 unavailable, 2 install or update needed, 3 available). */
    @JvmStatic
    fun sdkStatus(context: Context): Int {
        if (!supported()) return HealthConnectClient.SDK_UNAVAILABLE
        return HealthConnectClient.getSdkStatus(context, PROVIDER)
    }

    @JvmStatic
    fun available(context: Context): Boolean = sdkStatus(context) == HealthConnectClient.SDK_AVAILABLE

    private fun client(context: Context) = HealthConnectClient.getOrCreate(context, PROVIDER)

    @JvmField val SLEEP: String = HealthPermission.getReadPermission(SleepSessionRecord::class)
    @JvmField val RESTING_HR: String = HealthPermission.getReadPermission(RestingHeartRateRecord::class)
    @JvmField val HRV: String = HealthPermission.getReadPermission(HeartRateVariabilityRmssdRecord::class)
    @JvmField val WEIGHT: String = HealthPermission.getReadPermission(WeightRecord::class)
    @JvmField val EXERCISE: String = HealthPermission.getReadPermission(ExerciseSessionRecord::class)
    @JvmField val DISTANCE: String = HealthPermission.getReadPermission(DistanceRecord::class)
    const val BACKGROUND: String = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND

    /** The data permissions Jarvis asks for (read only). */
    @JvmStatic
    fun dataPermissions(): Set<String> = setOf(SLEEP, RESTING_HR, HRV, WEIGHT, EXERCISE, DISTANCE)

    /** Whether this phone's Health Connect can grant reading while the app is closed. */
    @JvmStatic
    fun backgroundReadAvailable(context: Context): Boolean = try {
        client(context).features.getFeatureStatus(
            HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND
        ) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE
    } catch (e: Exception) {
        false
    }

    /** What the permission sheet asks for: the data, plus background reading where offered. */
    @JvmStatic
    fun requestedPermissions(context: Context): Set<String> =
        if (backgroundReadAvailable(context)) dataPermissions() + BACKGROUND else dataPermissions()

    @JvmStatic
    fun permissionContract(): ActivityResultContract<Set<String>, Set<String>> =
        PermissionController.createRequestPermissionResultContract(PROVIDER)

    @JvmStatic
    fun grantedPermissions(context: Context): Set<String> = runBlocking {
        client(context).permissionController.getGrantedPermissions()
    }

    /** Withdraws every Health Connect permission Jarvis holds. */
    @JvmStatic
    fun revokeAll(context: Context) = runBlocking {
        client(context).permissionController.revokeAllPermissions()
    }

    private suspend fun <T : Record> readAll(
        client: HealthConnectClient,
        type: KClass<T>,
        filter: TimeRangeFilter,
    ): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        do {
            val response = client.readRecords(
                ReadRecordsRequest(recordType = type, timeRangeFilter = filter, pageToken = token)
            )
            out += response.records
            token = response.pageToken
        } while (!token.isNullOrEmpty())
        return out
    }

    private inline fun guarded(batch: Batch, what: String, block: () -> Unit) {
        try {
            block()
        } catch (e: Exception) {
            batch.errors += "$what: ${e.javaClass.simpleName}${e.message?.let { " ($it)" } ?: ""}"
        }
    }

    /** Reads every granted type between the two instants (epoch millis). */
    @JvmStatic
    fun read(context: Context, startMs: Long, endMs: Long, granted: Set<String>): Batch = runBlocking {
        val batch = Batch()
        val client = client(context)
        val filter = TimeRangeFilter.between(Instant.ofEpochMilli(startMs), Instant.ofEpochMilli(endMs))

        if (SLEEP in granted) guarded(batch, "sleep") {
            for (r in readAll(client, SleepSessionRecord::class, filter)) {
                val stages = r.stages.map {
                    HealthConnectMapping.Stage(it.stage, it.startTime.toEpochMilli(), it.endTime.toEpochMilli())
                }
                HealthConnectMapping.sleep(
                    r.metadata.id,
                    r.startTime.toEpochMilli(), r.startZoneOffset?.totalSeconds,
                    r.endTime.toEpochMilli(), r.endZoneOffset?.totalSeconds,
                    stages,
                )?.let { batch.sleeps += it }
            }
        }
        if (RESTING_HR in granted) guarded(batch, "resting heart rate") {
            for (r in readAll(client, RestingHeartRateRecord::class, filter)) {
                HealthConnectMapping.metric(
                    "resting_hr", "resting_hr", r.metadata.id, r.beatsPerMinute.toDouble(), "bpm",
                    r.time.toEpochMilli(), r.zoneOffset?.totalSeconds, r.metadata.dataOrigin.packageName,
                )?.let { batch.metrics += it }
            }
        }
        if (HRV in granted) guarded(batch, "heart rate variability") {
            for (r in readAll(client, HeartRateVariabilityRmssdRecord::class, filter)) {
                HealthConnectMapping.metric(
                    "hrv", "hrv", r.metadata.id, r.heartRateVariabilityMillis, "ms",
                    r.time.toEpochMilli(), r.zoneOffset?.totalSeconds, r.metadata.dataOrigin.packageName,
                )?.let { batch.metrics += it }
            }
        }
        if (WEIGHT in granted) guarded(batch, "weight") {
            for (r in readAll(client, WeightRecord::class, filter)) {
                HealthConnectMapping.metric(
                    "weight", "weight", r.metadata.id, r.weight.inKilograms, "kg",
                    r.time.toEpochMilli(), r.zoneOffset?.totalSeconds, r.metadata.dataOrigin.packageName,
                )?.let { batch.metrics += it }
            }
        }
        if (EXERCISE in granted) guarded(batch, "workouts") {
            for (r in readAll(client, ExerciseSessionRecord::class, filter)) {
                if (HealthConnectMapping.activityType(r.exerciseType) == null) {
                    batch.otherWorkouts++
                    continue
                }
                var meters: Double? = null
                if (DISTANCE in granted) {
                    // Health Connect's own aggregate removes overlaps between apps.
                    try {
                        val total = client.aggregate(
                            AggregateRequest(
                                metrics = setOf(DistanceRecord.DISTANCE_TOTAL),
                                timeRangeFilter = TimeRangeFilter.between(r.startTime, r.endTime),
                            )
                        )[DistanceRecord.DISTANCE_TOTAL]
                        meters = total?.inMeters
                    } catch (e: Exception) {
                        batch.errors += "distance: ${e.javaClass.simpleName}"
                    }
                }
                HealthConnectMapping.workout(
                    r.metadata.id, r.exerciseType, r.title,
                    r.startTime.toEpochMilli(), r.endTime.toEpochMilli(),
                    meters, r.metadata.dataOrigin.packageName,
                )?.let { batch.workouts += it }
            }
        }
        batch
    }
}
