package agent.bridge.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Client half of the wire protocol in mvp-1-spec.md 2.2.1. The reference
 * implementation is agent/protocol.py; byte layouts here must match it exactly.
 */
class ProtocolException(message: String) : Exception(message)

object Protocol {
    const val VERSION = 1
    const val NONCE_BYTES = 16
    private val C2S = "c2s".toByteArray(Charsets.US_ASCII)
    private val S2C = "s2c".toByteArray(Charsets.US_ASCII)
    private val random = SecureRandom()

    fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        parts.forEach { mac.update(it) }
        return mac.doFinal()
    }

    fun newNonce(): ByteArray = ByteArray(NONCE_BYTES).also { random.nextBytes(it) }

    private fun label(s: String) = s.toByteArray(Charsets.US_ASCII)

    fun serverProof(secret: ByteArray, nonceB: ByteArray, nonceS: ByteArray) =
        hmac(secret, label("server"), nonceB, nonceS)

    fun clientProof(secret: ByteArray, nonceB: ByteArray, nonceS: ByteArray) =
        hmac(secret, label("client"), nonceS, nonceB)

    fun sessionKey(secret: ByteArray, nonceB: ByteArray, nonceS: ByteArray) =
        hmac(secret, label("session"), nonceB, nonceS)

    fun helloFrame(nonceB: ByteArray): String = buildJsonObject {
        put("type", "hello"); put("v", VERSION); put("nonce_b", nonceB.toHex())
    }.toString()

    /** Verifies the server's proof; returns nonce_s. Nothing else may be sent before this succeeds. */
    fun readChallenge(raw: String, secret: ByteArray, nonceB: ByteArray): ByteArray {
        val frame = parseFrame(raw)
        if (frame.string("type") != "challenge" || frame["v"]?.jsonPrimitive?.intOrNull != VERSION) {
            throw ProtocolException("expected challenge v1")
        }
        val nonceS = frame.hexField("nonce_s", NONCE_BYTES)
        val proofS = frame.hexField("proof_s", 32)
        if (!MessageDigest.isEqual(proofS, serverProof(secret, nonceB, nonceS))) {
            throw ProtocolException("server proof invalid")
        }
        return nonceS
    }

    fun authFrame(secret: ByteArray, nonceB: ByteArray, nonceS: ByteArray): String = buildJsonObject {
        put("type", "auth"); put("proof_b", clientProof(secret, nonceB, nonceS).toHex())
    }.toString()

    fun parseFrame(raw: String): JsonObject = try {
        Json.parseToJsonElement(raw) as? JsonObject ?: throw ProtocolException("frame is not a JSON object")
    } catch (e: IllegalArgumentException) { // kotlinx SerializationException extends it
        throw ProtocolException("frame is not JSON")
    }

    fun clientChannel(key: ByteArray) = Channel(key, sendDir = C2S, recvDir = S2C)

    /** Test-only: the server half, so JVM tests can stand in for the agent. */
    fun serverChannel(key: ByteArray) = Channel(key, sendDir = S2C, recvDir = C2S)
}

/** Seals outgoing and opens incoming envelopes; one instance per session. */
class Channel internal constructor(
    private val key: ByteArray,
    private val sendDir: ByteArray,
    private val recvDir: ByteArray,
) {
    private var sendSeq = 0L
    private var recvSeq = 0L

    private fun mac(dir: ByteArray, seq: Long, body: String): ByteArray =
        Protocol.hmac(key, dir, ByteBuffer.allocate(8).putLong(seq).array(), body.toByteArray(Charsets.UTF_8))

    @Synchronized
    fun seal(message: JsonObject): String {
        sendSeq += 1
        val body = message.toString()
        return buildJsonObject {
            put("seq", sendSeq); put("body", body); put("mac", mac(sendDir, sendSeq, body).toHex())
        }.toString()
    }

    @Synchronized
    fun open(raw: String): JsonObject {
        val frame = Protocol.parseFrame(raw)
        val seqPrim = frame["seq"] as? JsonPrimitive
        val seq = seqPrim?.takeIf { !it.isString }?.longOrNull ?: throw ProtocolException("malformed envelope")
        val body = frame.string("body") ?: throw ProtocolException("malformed envelope")
        val mac = frame.hexField("mac", 32)
        // MAC before seq, so a forged seq cannot desynchronise us.
        if (!MessageDigest.isEqual(mac, mac(recvDir, seq, body))) throw ProtocolException("bad mac")
        if (seq != recvSeq + 1) throw ProtocolException("seq $seq after $recvSeq")
        recvSeq = seq
        val message = Protocol.parseFrame(body)
        if (message.string("type") == null) throw ProtocolException("message without type")
        return message
    }
}

internal fun JsonObject.string(name: String): String? =
    (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull

internal fun JsonObject.hexField(name: String, length: Int): ByteArray {
    val value = string(name)
    if (value == null || value.length != 2 * length) throw ProtocolException("$name must be $length bytes of hex")
    return value.hexToBytes() ?: throw ProtocolException("$name is not hex")
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

fun String.hexToBytes(): ByteArray? {
    if (length % 2 != 0) return null
    return ByteArray(length / 2) { i ->
        val hi = Character.digit(this[2 * i], 16)
        val lo = Character.digit(this[2 * i + 1], 16)
        if (hi < 0 || lo < 0) return null
        ((hi shl 4) or lo).toByte()
    }
}
