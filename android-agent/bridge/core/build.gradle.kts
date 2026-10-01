plugins {
    kotlin("jvm")
}

// Bytecode level 17 (what Android tooling expects), built with whatever JDK runs Gradle.
java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}
kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    // Both run unchanged on Android: OkHttp for the WebSocket, kotlinx JSON
    // elements (no code generation) for message bodies.
    api("com.squareup.okhttp3:okhttp:4.12.0")
    api("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.3")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}

tasks.test {
    useJUnitPlatform()
    // Interop tests start the real Python agent (../../agent/agent.py).
    systemProperty("agent.dir", rootDir.resolve("../agent").canonicalPath)
    testLogging { events("passed", "failed", "skipped"); showStandardStreams = false }
}
