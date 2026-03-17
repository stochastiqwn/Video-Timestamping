/**
 * WHEP (WebRTC HTTP Egress Protocol) client with absolute timestamp extraction.
 *
 * Supports two strategies for receiving timestamps:
 *
 * 1. **Encoded Transforms (Chromium)**: Uses RTCRtpScriptTransform to intercept
 *    encoded H.264 frames and parse SEI NAL units containing timestamps.
 *
 * 2. **DataChannel fallback (all browsers)**: Listens for a DataChannel carrying
 *    JSON timestamp messages alongside the video track.
 *
 * The client auto-detects which approach is available and uses both when possible.
 */

class WHEPClient {
  /**
   * @param {string} whepUrl - WHEP endpoint URL
   * @param {HTMLVideoElement} videoElement - Video element for playback
   * @param {function} onTimestamp - Callback: (timestampNs: BigInt, frameInfo: object) => void
   */
  constructor(whepUrl, videoElement, onTimestamp) {
    this.whepUrl = whepUrl;
    this.videoElement = videoElement;
    this.onTimestamp = onTimestamp || (() => {});
    this.pc = null;
    this.resourceUrl = null;
    this.seiWorker = null;
    this.supportsEncodedTransforms =
      typeof RTCRtpScriptTransform !== "undefined";
  }

  async connect() {
    this.pc = new RTCPeerConnection({
      // Use encoded insertable streams for Chromium
      encodedInsertableStreams: !this.supportsEncodedTransforms,
    });

    // Set up video track handler
    this.pc.ontrack = (event) => {
      console.log(`Track received: ${event.track.kind}`);

      if (event.track.kind === "video") {
        // Attach to video element for playback
        if (event.streams && event.streams[0]) {
          this.videoElement.srcObject = event.streams[0];
        } else {
          const stream = new MediaStream([event.track]);
          this.videoElement.srcObject = stream;
        }

        // Set up encoded transform for timestamp extraction
        this._setupEncodedTransform(event.receiver);
      }
    };

    // DataChannel fallback for timestamps
    this.pc.ondatachannel = (event) => {
      console.log(`DataChannel received: ${event.channel.label}`);
      const channel = event.channel;

      channel.onmessage = (msgEvent) => {
        try {
          const data = JSON.parse(msgEvent.data);
          if (data.timestamp_ns) {
            this.onTimestamp(BigInt(data.timestamp_ns), {
              frame: data.frame,
              source: "datachannel",
            });
          }
        } catch (e) {
          // Not JSON or not a timestamp message
        }
      };
    };

    // Add video transceiver (receive only)
    this.pc.addTransceiver("video", { direction: "recvonly" });

    // Create and send offer via WHEP
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);

    // Wait for ICE gathering to complete (or timeout)
    await this._waitForIceGathering(2000);

    const response = await fetch(this.whepUrl, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: this.pc.localDescription.sdp,
    });

    if (response.status !== 201) {
      const text = await response.text();
      throw new Error(`WHEP error: ${response.status} ${text}`);
    }

    const answerSdp = await response.text();
    this.resourceUrl = response.headers.get("Location");

    // Make resource URL absolute if relative
    if (this.resourceUrl && !this.resourceUrl.startsWith("http")) {
      const base = new URL(this.whepUrl);
      this.resourceUrl = new URL(this.resourceUrl, base).toString();
    }

    await this.pc.setRemoteDescription(
      new RTCSessionDescription({ type: "answer", sdp: answerSdp })
    );

    console.log("WHEP connection established");
    return this;
  }

  _setupEncodedTransform(receiver) {
    if (this.supportsEncodedTransforms) {
      // Modern API: RTCRtpScriptTransform
      try {
        this.seiWorker = new Worker("sei-parser-worker.js");
        this.seiWorker.onmessage = (event) => {
          if (event.data.type === "timestamp") {
            this.onTimestamp(BigInt(event.data.timestamp_ns), {
              frameType: event.data.frameType,
              rtpTimestamp: event.data.rtpTimestamp,
              source: "encoded-transform",
            });
          }
        };

        receiver.transform = new RTCRtpScriptTransform(this.seiWorker, {});
        console.log(
          "Encoded Transforms active — extracting timestamps from H.264 SEI"
        );
      } catch (e) {
        console.warn("Failed to set up Encoded Transform:", e);
      }
    } else if (receiver.createEncodedStreams) {
      // Legacy API: createEncodedStreams
      try {
        this.seiWorker = new Worker("sei-parser-worker.js");
        this.seiWorker.onmessage = (event) => {
          if (event.data.type === "timestamp") {
            this.onTimestamp(BigInt(event.data.timestamp_ns), {
              frameType: event.data.frameType,
              rtpTimestamp: event.data.rtpTimestamp,
              source: "encoded-transform-legacy",
            });
          }
        };

        const { readable, writable } = receiver.createEncodedStreams();
        this.seiWorker.postMessage(
          { type: "start", readable, writable },
          [readable, writable]
        );
        console.log(
          "Legacy Encoded Streams active — extracting timestamps from H.264 SEI"
        );
      } catch (e) {
        console.warn("Failed to set up legacy Encoded Streams:", e);
      }
    } else {
      console.warn(
        "Encoded Transforms not supported — timestamps available only via DataChannel"
      );
    }
  }

  async _waitForIceGathering(timeoutMs) {
    if (this.pc.iceGatheringState === "complete") return;

    return new Promise((resolve) => {
      const timeout = setTimeout(resolve, timeoutMs);
      this.pc.onicegatheringstatechange = () => {
        if (this.pc.iceGatheringState === "complete") {
          clearTimeout(timeout);
          resolve();
        }
      };
    });
  }

  async disconnect() {
    if (this.seiWorker) {
      this.seiWorker.terminate();
      this.seiWorker = null;
    }

    if (this.pc) {
      this.pc.close();
      this.pc = null;
    }

    // WHEP teardown
    if (this.resourceUrl) {
      try {
        await fetch(this.resourceUrl, { method: "DELETE" });
      } catch (e) {
        // Best-effort teardown
      }
      this.resourceUrl = null;
    }

    if (this.videoElement) {
      this.videoElement.srcObject = null;
    }

    console.log("Disconnected");
  }
}

// Export for use in HTML and modules
if (typeof window !== "undefined") {
  window.WHEPClient = WHEPClient;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { WHEPClient };
}
