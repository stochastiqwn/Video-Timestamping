/**
 * WebRTC Encoded Transform Worker — Extracts absolute timestamps from H.264 SEI NAL units.
 *
 * This worker intercepts encoded H.264 video frames via the RTCRtpScriptTransform API
 * (WebRTC Insertable Streams / Encoded Transforms), parses them for our timestamp
 * SEI NAL units, and posts the timestamps to the main thread.
 *
 * The frames are passed through unmodified to the decoder.
 */

// Must match the UUID in the Rust plugin and Python parser
const TIMESTAMP_UUID = new Uint8Array([
  0xa1, 0xb2, 0xc3, 0xd4, 0xe5, 0xf6, 0x47, 0x89, 0xab, 0xcd, 0xef, 0x01,
  0x23, 0x45, 0x67, 0x89,
]);

const NAL_TYPE_SEI = 6;

/**
 * Find start codes (00 00 01 or 00 00 00 01) in H.264 byte-stream data.
 * @param {Uint8Array} data
 * @returns {Array<{offset: number, scLen: number}>}
 */
function findStartCodes(data) {
  const results = [];
  const n = data.length;
  for (let i = 0; i < n - 2; i++) {
    if (data[i] === 0 && data[i + 1] === 0) {
      if (data[i + 2] === 1) {
        results.push({ offset: i + 3, scLen: 3 });
        i += 2;
      } else if (i + 3 < n && data[i + 2] === 0 && data[i + 3] === 1) {
        results.push({ offset: i + 4, scLen: 4 });
        i += 3;
      }
    }
  }
  return results;
}

/**
 * Check if two Uint8Arrays are equal.
 */
function arraysEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * Parse a u64 big-endian from 8 bytes.
 * JavaScript doesn't have native u64, but we can use BigInt for full precision
 * and convert to Number for practical use (safe up to 2^53).
 * @param {Uint8Array} bytes - 8 bytes
 * @returns {BigInt}
 */
function readU64BE(bytes) {
  let value = BigInt(0);
  for (let i = 0; i < 8; i++) {
    value = (value << BigInt(8)) | BigInt(bytes[i]);
  }
  return value;
}

/**
 * Parse SEI payload to extract our timestamp.
 * @param {Uint8Array} payload - SEI payload bytes (after NAL header byte)
 * @returns {BigInt|null} - Timestamp in nanoseconds, or null
 */
function parseSeiTimestamp(payload) {
  let offset = 0;
  const n = payload.length;

  while (offset < n) {
    // Parse payload type
    let payloadType = 0;
    while (offset < n && payload[offset] === 0xff) {
      payloadType += 255;
      offset++;
    }
    if (offset >= n) break;
    payloadType += payload[offset];
    offset++;

    // Parse payload size
    let payloadSize = 0;
    while (offset < n && payload[offset] === 0xff) {
      payloadSize += 255;
      offset++;
    }
    if (offset >= n) break;
    payloadSize += payload[offset];
    offset++;

    const payloadEnd = offset + payloadSize;
    if (payloadEnd > n) break;

    // Check for user_data_unregistered (type 5) with our UUID
    if (payloadType === 5 && payloadSize === 24) {
      const uuid = payload.slice(offset, offset + 16);
      if (arraysEqual(uuid, TIMESTAMP_UUID)) {
        const tsBytes = payload.slice(offset + 16, offset + 24);
        return readU64BE(tsBytes);
      }
    }

    offset = payloadEnd;
  }

  return null;
}

/**
 * Extract timestamp from an encoded H.264 frame.
 *
 * RTP depayloaded H.264 may arrive as:
 * - Single NAL unit (starts with NAL header byte, no start code)
 * - STAP-A aggregation packet (NAL type 24)
 * - FU-A fragmentation packet (NAL type 28)
 * - Or byte-stream format with start codes
 *
 * We handle both byte-stream format and single NAL unit format.
 *
 * @param {Uint8Array} data
 * @returns {BigInt|null}
 */
function extractTimestamp(data) {
  if (data.length < 2) return null;

  // Check if data starts with a start code (byte-stream format)
  const hasStartCode =
    (data[0] === 0 && data[1] === 0 && data[2] === 1) ||
    (data[0] === 0 && data[1] === 0 && data[2] === 0 && data[3] === 1);

  if (hasStartCode) {
    // Byte-stream format: iterate NAL units
    const startCodes = findStartCodes(data);
    for (let i = 0; i < startCodes.length; i++) {
      const naluStart = startCodes[i].offset;
      const naluEnd =
        i + 1 < startCodes.length
          ? startCodes[i + 1].offset - startCodes[i + 1].scLen
          : data.length;

      if (naluStart >= data.length) continue;
      const nalType = data[naluStart] & 0x1f;

      if (nalType === NAL_TYPE_SEI) {
        const ts = parseSeiTimestamp(data.subarray(naluStart + 1, naluEnd));
        if (ts !== null) return ts;
      }
    }
  } else {
    // Single NAL unit or aggregation packet
    const nalType = data[0] & 0x1f;

    if (nalType === NAL_TYPE_SEI) {
      return parseSeiTimestamp(data.subarray(1));
    }

    // STAP-A (type 24): multiple NAL units aggregated
    if (nalType === 24) {
      let offset = 1; // skip STAP-A header
      while (offset + 2 < data.length) {
        const naluSize = (data[offset] << 8) | data[offset + 1];
        offset += 2;
        if (offset + naluSize > data.length) break;

        const subNalType = data[offset] & 0x1f;
        if (subNalType === NAL_TYPE_SEI) {
          const ts = parseSeiTimestamp(
            data.subarray(offset + 1, offset + naluSize)
          );
          if (ts !== null) return ts;
        }
        offset += naluSize;
      }
    }
  }

  return null;
}

// RTCRtpScriptTransform handler
if (typeof RTCTransformEvent !== "undefined") {
  // Modern API: RTCRtpScriptTransform
  self.onrtctransform = (event) => {
    const transformer = event.transformer;
    const readable = transformer.readable;
    const writable = transformer.writable;

    const transform = new TransformStream({
      transform(encodedFrame, controller) {
        const data = new Uint8Array(encodedFrame.data);
        const timestamp = extractTimestamp(data);

        if (timestamp !== null) {
          self.postMessage({
            type: "timestamp",
            timestamp_ns: timestamp.toString(),
            frameType: encodedFrame.type,
            rtpTimestamp: encodedFrame.timestamp,
          });
        }

        // Pass frame through unmodified
        controller.enqueue(encodedFrame);
      },
    });

    readable.pipeThrough(transform).pipeTo(writable);
  };
} else {
  // Legacy API: createEncodedStreams (older Chromium versions)
  self.onmessage = (event) => {
    if (event.data.type === "start") {
      const { readable, writable } = event.data;

      const transform = new TransformStream({
        transform(encodedFrame, controller) {
          const data = new Uint8Array(encodedFrame.data);
          const timestamp = extractTimestamp(data);

          if (timestamp !== null) {
            self.postMessage({
              type: "timestamp",
              timestamp_ns: timestamp.toString(),
              frameType: encodedFrame.type,
              rtpTimestamp: encodedFrame.timestamp,
            });
          }

          controller.enqueue(encodedFrame);
        },
      });

      readable.pipeThrough(transform).pipeTo(writable);
    }
  };
}
