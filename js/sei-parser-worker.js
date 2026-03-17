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
 * Check if 16 bytes at the given offset match the TIMESTAMP_UUID.
 * Avoids creating a slice and comparing arrays.
 * @param {Uint8Array} data
 * @param {number} offset
 * @returns {boolean}
 */
function uuidMatchesAt(data, offset) {
  for (let i = 0; i < 16; i++) {
    if (data[offset + i] !== TIMESTAMP_UUID[i]) return false;
  }
  return true;
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
      if (uuidMatchesAt(payload, offset)) {
        // Read u64 big-endian via DataView (avoids manual BigInt loop)
        const view = new DataView(
          payload.buffer,
          payload.byteOffset + offset + 16,
          8
        );
        return view.getBigUint64(0, /* littleEndian */ false);
      }
    }

    offset = payloadEnd;
  }

  return null;
}

/**
 * Find the next start code (00 00 01 or 00 00 00 01) starting from `from`.
 * Returns {offset, scLen} or null.
 * @param {Uint8Array} data
 * @param {number} from
 * @returns {{offset: number, scLen: number}|null}
 */
function findNextStartCode(data, from) {
  const n = data.length;
  for (let i = from; i < n - 2; i++) {
    if (data[i] === 0 && data[i + 1] === 0) {
      if (data[i + 2] === 1) {
        return { offset: i + 3, scLen: 3 };
      }
      if (i + 3 < n && data[i + 2] === 0 && data[i + 3] === 1) {
        return { offset: i + 4, scLen: 4 };
      }
    }
  }
  return null;
}

/**
 * Extract timestamp from an encoded H.264 frame.
 *
 * Iterates NAL units lazily — stops as soon as a timestamp is found or
 * a VCL NAL (types 1-5) is reached (SEI always precedes VCL).
 *
 * Handles byte-stream (Annex B), single NAL unit, and STAP-A formats.
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
    // Byte-stream format: iterate NAL units lazily
    let sc = findNextStartCode(data, 0);
    while (sc !== null) {
      const naluStart = sc.offset;
      if (naluStart >= data.length) break;

      const nalType = data[naluStart] & 0x1f;

      // VCL NAL types 1-5: no more SEI can follow, stop early
      if (nalType >= 1 && nalType <= 5) return null;

      // Find end of this NAL (next start code or end of data)
      const nextSc = findNextStartCode(data, naluStart);
      const naluEnd = nextSc !== null ? nextSc.offset - nextSc.scLen : data.length;

      if (nalType === NAL_TYPE_SEI) {
        const ts = parseSeiTimestamp(data.subarray(naluStart + 1, naluEnd));
        if (ts !== null) return ts;
      }

      sc = nextSc;
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

        // VCL: stop early
        if (subNalType >= 1 && subNalType <= 5) return null;

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
