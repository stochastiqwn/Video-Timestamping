/// H.264 SEI (Supplemental Enhancement Information) utilities for absolute timestamp embedding.
///
/// We use SEI NAL unit type 6, with payload type 5 (user_data_unregistered).
/// The payload contains a 16-byte UUID followed by an 8-byte big-endian u64
/// representing nanoseconds since the Unix epoch (UTC).
///
/// Binary layout of the complete NAL unit (byte-stream format):
///
/// ```text
/// [00 00 00 01]        4-byte start code
/// [06]                 NAL header: nal_unit_type=6 (SEI)
/// [05]                 SEI payload type = 5 (user_data_unregistered)
/// [18]                 SEI payload size = 24 (16 UUID + 8 timestamp)
/// [UUID: 16 bytes]     Our fixed UUID
/// [TS: 8 bytes]        u64 big-endian nanoseconds since Unix epoch
/// [80]                 RBSP stop bit + alignment
/// ```
use std::time::{SystemTime, UNIX_EPOCH};

/// Fixed UUID identifying our timestamp SEI messages.
/// Generated once; receivers must know this UUID to extract timestamps.
/// Value: a1b2c3d4-e5f6-4789-abcd-ef0123456789
pub const TIMESTAMP_UUID: [u8; 16] = [
    0xa1, 0xb2, 0xc3, 0xd4, 0xe5, 0xf6, 0x47, 0x89, 0xab, 0xcd, 0xef, 0x01, 0x23, 0x45, 0x67,
    0x89,
];

/// H.264 NAL unit type for SEI.
const NAL_TYPE_SEI: u8 = 6;

/// SEI payload type for user_data_unregistered.
const SEI_PAYLOAD_TYPE_USER_DATA_UNREGISTERED: u8 = 5;

/// Size of our SEI payload: 16 (UUID) + 8 (timestamp).
const SEI_PAYLOAD_SIZE: u8 = 24;

/// Get current wall-clock time as nanoseconds since Unix epoch.
pub fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before Unix epoch")
        .as_nanos() as u64
}

/// Create a complete SEI NAL unit (byte-stream format with 4-byte start code)
/// containing an absolute timestamp.
pub fn create_sei_timestamp_nalu(timestamp_ns: u64) -> Vec<u8> {
    let mut nalu = Vec::with_capacity(4 + 1 + 1 + 1 + 16 + 8 + 1);

    // 4-byte start code
    nalu.extend_from_slice(&[0x00, 0x00, 0x00, 0x01]);

    // NAL header: forbidden_zero_bit=0, nal_ref_idc=0, nal_unit_type=6 (SEI)
    nalu.push(NAL_TYPE_SEI);

    // SEI payload type
    nalu.push(SEI_PAYLOAD_TYPE_USER_DATA_UNREGISTERED);

    // SEI payload size
    nalu.push(SEI_PAYLOAD_SIZE);

    // UUID
    nalu.extend_from_slice(&TIMESTAMP_UUID);

    // Timestamp: u64 big-endian
    nalu.extend_from_slice(&timestamp_ns.to_be_bytes());

    // RBSP trailing bits: stop bit (1) + 7 alignment zeros = 0x80
    nalu.push(0x80);

    nalu
}

/// Parse a single SEI NAL unit payload (after the NAL header byte) and extract
/// our timestamp if the UUID matches.
///
/// `sei_payload` should start at the first byte after the NAL header (i.e.,
/// the SEI payload type byte).
pub fn parse_sei_timestamp_from_payload(sei_payload: &[u8]) -> Option<u64> {
    let mut offset = 0;

    // There can be multiple SEI messages in one NAL unit; iterate through them.
    while offset < sei_payload.len() {
        // Parse payload type (could be multi-byte for type >= 255, but ours is 5)
        let mut payload_type: u32 = 0;
        while offset < sei_payload.len() && sei_payload[offset] == 0xFF {
            payload_type += 255;
            offset += 1;
        }
        if offset >= sei_payload.len() {
            break;
        }
        payload_type += sei_payload[offset] as u32;
        offset += 1;

        // Parse payload size (could be multi-byte for size >= 255)
        let mut payload_size: u32 = 0;
        while offset < sei_payload.len() && sei_payload[offset] == 0xFF {
            payload_size += 255;
            offset += 1;
        }
        if offset >= sei_payload.len() {
            break;
        }
        payload_size += sei_payload[offset] as u32;
        offset += 1;

        let payload_end = offset + payload_size as usize;
        if payload_end > sei_payload.len() {
            break;
        }

        // Check if this is user_data_unregistered with our UUID
        if payload_type == SEI_PAYLOAD_TYPE_USER_DATA_UNREGISTERED as u32
            && payload_size == SEI_PAYLOAD_SIZE as u32
        {
            let uuid_slice = &sei_payload[offset..offset + 16];
            if uuid_slice == TIMESTAMP_UUID {
                let ts_bytes: [u8; 8] = sei_payload[offset + 16..offset + 24]
                    .try_into()
                    .ok()?;
                return Some(u64::from_be_bytes(ts_bytes));
            }
        }

        offset = payload_end;
    }

    None
}

/// Iterator over NAL units in byte-stream format (Annex B: start codes 00 00 01 or 00 00 00 01).
/// Yields (offset_after_start_code, nalu_data_without_start_code) for each NAL unit.
pub struct NaluIterator<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> NaluIterator<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }

    /// Find the next start code starting from `pos`. Returns the offset of the
    /// first byte after the start code, and the start code length (3 or 4).
    fn find_start_code(&self, from: usize) -> Option<(usize, usize)> {
        let d = self.data;
        let mut i = from;
        while i + 2 < d.len() {
            if d[i] == 0x00 && d[i + 1] == 0x00 {
                if d[i + 2] == 0x01 {
                    return Some((i + 3, 3));
                }
                if i + 3 < d.len() && d[i + 2] == 0x00 && d[i + 3] == 0x01 {
                    return Some((i + 4, 4));
                }
            }
            i += 1;
        }
        None
    }
}

impl<'a> Iterator for NaluIterator<'a> {
    /// (start_code_offset, nalu_data_including_header_byte)
    type Item = (usize, &'a [u8]);

    fn next(&mut self) -> Option<Self::Item> {
        // Find start of current NAL unit
        let (nalu_start, _sc_len) = self.find_start_code(self.pos)?;

        // Find start of next NAL unit (or end of data)
        let nalu_end = if let Some((next_start, next_sc_len)) = self.find_start_code(nalu_start) {
            next_start - next_sc_len
        } else {
            self.data.len()
        };

        self.pos = nalu_end;

        // Strip trailing zeros from the NAL unit (they belong to the next start code)
        let nalu_data = &self.data[nalu_start..nalu_end];
        Some((nalu_start, nalu_data))
    }
}

/// Scan a byte-stream H.264 access unit and return the first timestamp found.
///
/// Terminates early once a timestamp is found or once a VCL NAL unit (types 1-5)
/// is reached, since SEI NAL units always precede VCL NAL units in an access unit.
pub fn find_sei_timestamp(data: &[u8]) -> Option<u64> {
    for (_offset, nalu) in NaluIterator::new(data) {
        if nalu.is_empty() {
            continue;
        }
        let nal_type = nalu[0] & 0x1F;
        // VCL NAL types 1-5: no more SEI can follow, stop scanning
        if (1..=5).contains(&nal_type) {
            return None;
        }
        if nal_type == NAL_TYPE_SEI {
            if let Some(ts) = parse_sei_timestamp_from_payload(&nalu[1..]) {
                return Some(ts);
            }
        }
    }
    None
}

/// Find the byte offset of the first VCL (Video Coding Layer) NAL unit in
/// byte-stream format data. VCL NAL types are 1-5 (non-IDR slice, slice
/// partition A/B/C, IDR slice).
/// Returns the offset of the start code (00 00 00 01 or 00 00 01) preceding the VCL NAL.
pub fn find_first_vcl_start_code_offset(data: &[u8]) -> Option<usize> {
    for (nalu_start, nalu) in NaluIterator::new(data) {
        if nalu.is_empty() {
            continue;
        }
        let nal_type = nalu[0] & 0x1F;
        if (1..=5).contains(&nal_type) {
            // Walk backwards from nalu_start to find the start code
            let mut sc_start = nalu_start - 3; // at least 00 00 01
            if sc_start > 0 && data[sc_start - 1] == 0x00 {
                sc_start -= 1; // 00 00 00 01
            }
            return Some(sc_start);
        }
    }
    None
}

/// Inject a timestamp SEI NAL unit into an H.264 byte-stream access unit,
/// placing it before the first VCL NAL unit.
///
/// Returns a new Vec<u8> with the SEI inserted.
pub fn inject_timestamp_sei(data: &[u8], timestamp_ns: u64) -> Vec<u8> {
    let sei_nalu = create_sei_timestamp_nalu(timestamp_ns);

    if let Some(vcl_offset) = find_first_vcl_start_code_offset(data) {
        // Insert SEI before the first VCL NAL
        let mut result = Vec::with_capacity(data.len() + sei_nalu.len());
        result.extend_from_slice(&data[..vcl_offset]);
        result.extend_from_slice(&sei_nalu);
        result.extend_from_slice(&data[vcl_offset..]);
        result
    } else {
        // No VCL NAL found; prepend SEI at the beginning
        let mut result = Vec::with_capacity(data.len() + sei_nalu.len());
        result.extend_from_slice(&sei_nalu);
        result.extend_from_slice(data);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_and_parse_roundtrip() {
        let ts: u64 = 1_700_000_000_000_000_000; // ~2023-11-14
        let nalu = create_sei_timestamp_nalu(ts);

        // Verify start code
        assert_eq!(&nalu[..4], &[0x00, 0x00, 0x00, 0x01]);
        // Verify NAL type is SEI
        assert_eq!(nalu[4] & 0x1F, NAL_TYPE_SEI);

        // Parse it back
        let parsed = parse_sei_timestamp_from_payload(&nalu[5..nalu.len() - 1]);
        assert_eq!(parsed, Some(ts));
    }

    #[test]
    fn test_find_sei_in_access_unit() {
        let ts: u64 = 1_234_567_890_000_000_000;
        let sei = create_sei_timestamp_nalu(ts);

        // Simulate an access unit: SPS + SEI + IDR
        let mut au = Vec::new();
        // SPS (NAL type 7)
        au.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0x00, 0x1e]);
        // Our SEI
        au.extend_from_slice(&sei);
        // IDR slice (NAL type 5)
        au.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x80, 0x40]);

        assert_eq!(find_sei_timestamp(&au), Some(ts));
    }

    #[test]
    fn test_inject_before_vcl() {
        let ts: u64 = 9_999_999_999;

        // Access unit: SPS + IDR (no SEI yet)
        let mut au = Vec::new();
        // SPS
        au.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0x00, 0x1e]);
        // IDR
        au.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x65, 0x88, 0x80, 0x40]);

        let result = inject_timestamp_sei(&au, ts);

        // Should now contain our timestamp
        assert_eq!(find_sei_timestamp(&result), Some(ts));

        // The SPS should still be at the beginning
        assert_eq!(&result[..5], &[0x00, 0x00, 0x00, 0x01, 0x67]);
    }

    #[test]
    fn test_nalu_iterator() {
        let mut data = Vec::new();
        // NAL 1 (3-byte start code)
        data.extend_from_slice(&[0x00, 0x00, 0x01, 0x67, 0xAA]);
        // NAL 2 (4-byte start code)
        data.extend_from_slice(&[0x00, 0x00, 0x00, 0x01, 0x65, 0xBB]);

        let nalus: Vec<_> = NaluIterator::new(&data).collect();
        assert_eq!(nalus.len(), 2);
        assert_eq!(nalus[0].1[0] & 0x1F, 7); // SPS
        assert_eq!(nalus[1].1[0] & 0x1F, 5); // IDR
    }
}
