package agent.bridge.app

import agent.bridge.core.AuditLog
import android.content.Context
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File

/** Append-only JSONL in app-private storage; Bridge's log is the reference (mvp-1-spec 4). */
class AuditLogFile(context: Context) : AuditLog {
    private val file = File(context.filesDir, "audit.jsonl")

    @Synchronized
    override fun record(event: String, fields: Map<String, Any?>) {
        val line = buildJsonObject {
            put("ts", System.currentTimeMillis())
            put("event", event)
            fields.forEach { (k, v) ->
                put(k, when (v) {
                    null -> JsonNull
                    is Number -> JsonPrimitive(v)
                    is Boolean -> JsonPrimitive(v)
                    else -> JsonPrimitive(v.toString())
                })
            }
        }.toString()
        file.appendText(line + "\n")
    }
}
