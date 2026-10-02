package agent.bridge.app

import agent.bridge.core.AuditLog
import agent.bridge.core.BridgeCore
import android.content.Context

/** Process-wide handle so the action receiver and the screen can reach the running core. */
object AgentBridge {
    @Volatile
    var core: BridgeCore<ReplyHandle>? = null

    /** Live numbers for the main screen; in memory only. */
    val stats = BridgeStats()
}

class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)

    /** Cloud-LLM consent (mvp-1-spec 5). Nothing is forwarded while false. */
    var consent: Boolean
        get() = prefs.getBoolean("consent", false)
        set(value) = prefs.edit().putBoolean("consent", value).apply()

    val port: Int get() = prefs.getInt("port", 8000)
}

/** Counters and recent events derived from Bridge's own audit trail. */
class BridgeStats {
    data class Event(val time: Long, val name: String, val sender: String?)

    @Volatile var listenerBound = false
    @Volatile var agentConnected = false
    @Volatile var connectedSince = 0L
    @Volatile var untrustedServer = false
    private val counts = HashMap<String, Int>()
    private val recent = ArrayDeque<Event>()

    @Synchronized
    fun record(event: String, sender: String?) {
        counts[event] = (counts[event] ?: 0) + 1
        when (event) {
            "session_opened" -> { agentConnected = true; connectedSince = System.currentTimeMillis(); untrustedServer = false }
            "session_closed" -> agentConnected = false
            "handshake_failed" -> untrustedServer = true
        }
        recent.addFirst(Event(System.currentTimeMillis(), event, sender))
        while (recent.size > 30) recent.removeLast()
    }

    @Synchronized fun count(event: String): Int = counts[event] ?: 0

    @Synchronized fun recentEvents(): List<Event> = recent.toList()
}

/** Writes the audit file and feeds [BridgeStats]; message text never reaches the screen's event list. */
class StatsAuditLog(private val file: AuditLog, private val stats: BridgeStats) : AuditLog {
    override fun record(event: String, fields: Map<String, Any?>) {
        file.record(event, fields)
        stats.record(event, fields["sender"]?.toString())
    }
}
