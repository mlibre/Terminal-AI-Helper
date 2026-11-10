//! A disk cache of a built engine, keyed on the store it was built from.
//!
//! Port of `tai/engcache.py`. `tai suggest`, `tai jev` and `tai web` each
//! start a fresh process, and until now each one rebuilt the whole engine from
//! SQLite before it could answer. The engine is plain data, so it caches: the
//! first process after a change builds and saves, every process after that
//! loads in a few milliseconds.
//!
//! The Python implementation cached with `marshal`; this one caches with a
//! self-describing JSON snapshot under its own magic and version, so a v2
//! cache of any shape is simply a miss to this reader and the engine rebuilds.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::engine::{CmdStats, Engine};

// Bump when the cached shape changes.
pub const VERSION: u32 = 4;
const MAGIC: &str = "tai-engine-r";

fn cache_path(db: &str) -> PathBuf {
    let name = sha1_16(db);
    PathBuf::from(db)
        .parent()
        .map(|p| p.join(format!("engine-{}.cache", name)))
        .unwrap_or_else(|| PathBuf::from(format!("engine-{}.cache", name)))
}

fn sha1_16(s: &str) -> String {
    // A tiny SHA-1 (the same first 16 hex chars Python's hashlib produced),
    // because the file name is the only thing that must agree with the past —
    // and even if it did not, a miss just rebuilds.
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let data = s.as_bytes();
    let ml = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&ml.to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A827999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    h.iter().map(|x| format!("{:08x}", x)).collect::<String>()[..16].to_string()
}

fn snapshot(eng: &Engine) -> serde_json::Value {
    let names: Vec<&String> = eng.cmds.keys().collect();
    let fields: Vec<serde_json::Value> = names
        .iter()
        .map(|c| {
            let st = &eng.cmds[*c];
            serde_json::json!([
                st.freq,
                st.last_ts,
                serde_json::to_value(&st.cwd).unwrap_or_default(),
                serde_json::to_value(&st.repo).unwrap_or_default(),
                serde_json::to_value(&st.branch).unwrap_or_default(),
                st.hour,
                st.success,
                st.fail,
                st.nf
            ])
        })
        .collect();
    // The trigram table is keyed on a (word, word) pair, which JSON cannot
    // spell as an object key; it travels as [a, b, counts] rows instead.
    let tri: Vec<serde_json::Value> = eng
        .token_trigram
        .iter()
        .map(|((a, b), counts)| {
            serde_json::json!([a, b, serde_json::to_value(counts).unwrap_or_default()])
        })
        .collect();
    serde_json::json!({
        "cmds": [names, fields],
        "seq": eng.seq,
        "big": eng.token_bigram,
        "tri": tri,
        "sorted": eng.sorted_cmds,
        "lower": eng.sorted_lower,
    })
}

fn load_snapshot(blob: &serde_json::Value) -> Option<Engine> {
    let cmds = blob.get("cmds")?.as_array()?;
    let names = cmds.first()?.as_array()?;
    let fields = cmds.get(1)?.as_array()?;
    let mut eng = Engine::new();
    for (name, row) in names.iter().zip(fields.iter()) {
        let row = row.as_array()?;
        if row.len() != 9 {
            return None;
        }
        let mut st = CmdStats::default();
        st.freq = row[0].as_i64()?;
        st.last_ts = row[1].as_i64()?;
        st.cwd = serde_json::from_value(row[2].clone()).ok()?;
        st.repo = serde_json::from_value(row[3].clone()).ok()?;
        st.branch = serde_json::from_value(row[4].clone()).ok()?;
        st.hour = [
            row[5].as_array()?.first()?.as_i64()?,
            row[5].as_array()?.get(1)?.as_i64()?,
            row[5].as_array()?.get(2)?.as_i64()?,
            row[5].as_array()?.get(3)?.as_i64()?,
        ];
        st.success = row[6].as_i64()?;
        st.fail = row[7].as_i64()?;
        st.nf = row[8].as_i64()?;
        eng.order.push(name.as_str()?.to_string());
        eng.cmds.insert(name.as_str()?.to_string(), st);
    }
    eng.seq = serde_json::from_value(blob.get("seq")?.clone()).ok()?;
    eng.token_bigram = serde_json::from_value(blob.get("big")?.clone()).ok()?;
    let mut tri: HashMap<(String, String), HashMap<String, i64>> = HashMap::new();
    for row in blob.get("tri")?.as_array()? {
        let parts = row.as_array()?;
        if parts.len() != 3 {
            return None;
        }
        let a = parts[0].as_str()?.to_string();
        let b = parts[1].as_str()?.to_string();
        let counts: HashMap<String, i64> = serde_json::from_value(parts[2].clone()).ok()?;
        tri.insert((a, b), counts);
    }
    eng.token_trigram = tri;
    eng.sorted_cmds = serde_json::from_value(blob.get("sorted")?.clone()).ok()?;
    eng.sorted_lower = serde_json::from_value(blob.get("lower")?.clone()).ok()?;
    Some(eng)
}

/// The built engine for `db_path`, loading a cached one when it is current.
/// Everything is best-effort: an unreadable cache, a read-only data directory
/// or a mid-write crash all fall back to building. TAI_NO_ENGINE_CACHE=1
/// turns it off.
pub fn get(db_path: &str, build: impl FnOnce() -> Engine) -> Engine {
    if std::env::var("TAI_NO_ENGINE_CACHE").unwrap_or_default() == "1" {
        return build();
    }
    let st = match fs::metadata(db_path) {
        Ok(m) => m,
        Err(_) => return build(), // no store yet: nothing to cache against
    };
    use std::os::unix::fs::MetadataExt;
    let key = (st.size(), st.mtime_nsec());
    let cp = cache_path(db_path);
    if let Ok(blob) = fs::read(&cp) {
        if let Ok(text) = std::str::from_utf8(&blob) {
            if let Some(first_line) = text.lines().next() {
                let parts: Vec<&str> = first_line.split_whitespace().collect();
                if parts.len() == 4
                    && parts[0] == MAGIC
                    && parts[1].parse::<u32>().ok() == Some(VERSION)
                    && parts[2].parse::<u64>().ok() == Some(key.0)
                    && parts[3].parse::<i64>().ok() == Some(key.1 as i64)
                {
                    let rest = &blob[first_line.len() + 1..];
                    if let Ok(v) = serde_json::from_slice::<serde_json::Value>(rest) {
                        if let Some(eng) = load_snapshot(&v) {
                            return eng;
                        }
                    }
                }
            }
        }
    }
    let mut eng = build();
    // The snapshot is taken after ensure_sorted, so the sorted contract comes
    // back squared away and nothing re-sorts on load.
    eng.ensure_sorted();
    let tmp = cp.with_extension(format!("tmp{}", std::process::id()));
    let body = serde_json::to_vec(&snapshot(&eng)).unwrap_or_default();
    let header = format!("{} {} {} {}\n", MAGIC, VERSION, key.0, key.1);
    let mut out = header.into_bytes();
    out.extend_from_slice(&body);
    if fs::write(&tmp, out).is_ok() && fs::rename(&tmp, &cp).is_ok() {
        return eng;
    }
    let _ = fs::remove_file(&tmp);
    eng
}

pub fn _unused(_: &Path) {}
