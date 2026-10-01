package agent.bridge.core

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/** Byte-level agreement with agent/protocol.py (vectors generated from it). */
class ProtocolTest {
    private val secret = ByteArray(32) { it.toByte() }
    private val nonceB = ByteArray(16) { (100 + it).toByte() }
    private val nonceS = ByteArray(16) { (200 + it).toByte() }

    @Test
    fun `handshake values match the Python reference`() {
        assertEquals("99957680b05620921855f91672f6c11e9e070c3f97a54c68167bebb6554d1aa8",
            Protocol.serverProof(secret, nonceB, nonceS).toHex())
        assertEquals("60b72f2eadc0fa7b93bcb8e506897f584184831869294149dafeca902d8ebcf5",
            Protocol.clientProof(secret, nonceB, nonceS).toHex())
        assertEquals("3535932fedf2aa8778049a08fbe2c08bab5ceabe3367f3091d5cbfcc961e18f1",
            Protocol.sessionKey(secret, nonceB, nonceS).toHex())
    }

    @Test
    fun `opens a frame sealed by the Python server`() {
        val body = """{"type": "propose_reply", "reply_token": "rt_x", "proposed_text": "تمام، سأكون هناك"}"""
        val frame = buildJsonObject {
            put("seq", 1); put("body", body)
            put("mac", "1486302342547aa903b3425ee1f616065d4ad8c068c0d31a1ef29d0efa01ae9b")
        }.toString()
        val channel = Protocol.clientChannel(Protocol.sessionKey(secret, nonceB, nonceS))
        val message = channel.open(frame)
        assertEquals(JsonPrimitive("تمام، سأكون هناك"), message["proposed_text"])
    }

    @Test
    fun `challenge with a wrong proof is rejected`() {
        val frame = buildJsonObject {
            put("type", "challenge"); put("v", 1); put("nonce_s", nonceS.toHex()); put("proof_s", "00".repeat(32))
        }.toString()
        assertFailsWith<ProtocolException> { Protocol.readChallenge(frame, secret, nonceB) }
    }

    @Test
    fun `valid challenge returns the server nonce`() {
        val frame = buildJsonObject {
            put("type", "challenge"); put("v", 1); put("nonce_s", nonceS.toHex())
            put("proof_s", Protocol.serverProof(secret, nonceB, nonceS).toHex())
        }.toString()
        assertContentEquals(nonceS, Protocol.readChallenge(frame, secret, nonceB))
    }

    @Test
    fun `tampered, replayed and reflected frames are rejected`() {
        val key = Protocol.sessionKey(secret, nonceB, nonceS)
        val server = Protocol.serverChannel(key)
        val msg = buildJsonObject { put("type", "propose_reply") }

        val first = server.seal(msg)
        val client = Protocol.clientChannel(key)
        client.open(first)
        assertFailsWith<ProtocolException>("replay") { client.open(first) }

        val tampered = server.seal(msg).replace("propose_reply", "propose_replY")
        assertFailsWith<ProtocolException>("tamper") { Protocol.clientChannel(key).open(tampered) }

        // A frame the client itself sealed (c2s) must not open as s2c.
        val own = Protocol.clientChannel(key).seal(msg)
        assertFailsWith<ProtocolException>("reflection") { Protocol.clientChannel(key).open(own) }
    }

    @Test
    fun `hex helpers reject garbage`() {
        assertEquals(null, "zz".hexToBytes())
        assertEquals(null, "abc".hexToBytes())
        assertContentEquals(byteArrayOf(0x0a, 0xff.toByte()), "0aff".hexToBytes())
    }
}
