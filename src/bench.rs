//! tai bench — embedded index + direct engine diagnostics.
//!
//! Port of `tai/bench.py`. Every number here answers "is the engine slow",
//! and the engine is not on the keystroke path — the shell is, and it reads
//! the index once per shell start, which is why its *size* is the cost that
//! matters. The one measurement the design turns on is the shell sourcing its
//! own index, timed with the start-up inside the number.

use crate::paths;
use crate::predictor;
use crate::store;

fn rss_kb() -> Option<i64> {
    // A diagnostic that prints 0 when it cannot read the number is worse than
    // one that prints nothing: 0kB is a measurement, and it is wrong.
    let data = std::fs::read_to_string("/proc/self/status").ok()?;
    for line in data.lines() {
        if let Some(rest) = line.strip_prefix("VmRSS:") {
            return rest.split_whitespace().next()?.parse().ok();
        }
    }
    None
}

fn load(limit: usize) -> (crate::engine::Engine, usize, f64, i64, i64) {
    let t0 = std::time::Instant::now();
    let rows = match store::load_rows(limit as i64) {
        Ok(r) => r,
        Err(e) => {
            let note = store::schema_note();
            println!("cannot read the history database: {}", if note.is_empty() { e } else { note });
            std::process::exit(1);
        }
    };
    let mut eng = crate::engine::Engine::new();
    eng.build_from_rows(&rows);
    let took = t0.elapsed().as_secs_f64() * 1000.0;
    let (n, d) = store::count();
    (eng, rows.len(), took, n, d)
}

pub fn main(limit: usize) {
    println!("db: {}", paths::db_location());
    let (mut eng, n_rows, load_ms, n, d) = load(limit);
    let rss = rss_kb();
    println!(
        "rows={} distinct={} loaded={} in {:.0}ms rss={}kB",
        n,
        d,
        n_rows,
        load_ms,
        rss.map(|r| r.to_string()).unwrap_or_else(|| "?".to_string())
    );
    let prefixes = ["", "g", "gi", "git ", "git p", "docker ", "docker c", "d", "npm ", "python3 "];
    let mut lat: Vec<f64> = Vec::new();
    for _ in 0..5 {
        for prefix in prefixes {
            let t0 = std::time::Instant::now();
            let _ = eng.suggest(prefix, "", "", "", &[], 1, 0.9, None, false, &mut |_| false, None);
            lat.push(t0.elapsed().as_secs_f64() * 1000.0);
        }
    }
    lat.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    println!(
        "engine suggest: p50={:.2}ms p95={:.2}ms",
        lat[lat.len() / 2],
        lat[((lat.len() as f64 * 0.95) as usize).saturating_sub(1)]
    );
    let zsh = paths::index_location();
    let bash = paths::bash_index_location();
    println!(
        "embedded indexes: zsh={} bash={}",
        std::path::Path::new(&zsh).exists(),
        std::path::Path::new(&bash).exists()
    );
    report_source_time(&zsh, &bash);
}

/// How long each shell takes to read the index — the startup cost itself.
fn report_source_time(zsh_index: &str, bash_index: &str) {
    let zsh = util_which("zsh");
    let bash = util_which("bash");
    let cases: [(&str, &str, Option<String>); 2] = [
        ("zsh", zsh_index, zsh),
        ("bash", bash_index, bash),
    ];
    for (label, path, shell) in cases {
        let shell = match shell {
            Some(s) => s,
            None => continue,
        };
        if !std::path::Path::new(path).exists() {
            continue;
        }
        let mut best: Option<f64> = None;
        for _ in 0..3 {
            // the first start pays for the page cache
            let t0 = std::time::Instant::now();
            let script = format!("source {}", path);
            let (out, err) = crate::util::run_with_timeout(&[&shell, "-f", "-c", &script], 60.0, None, &[]);
            let took = t0.elapsed().as_secs_f64() * 1000.0;
            // bash wants --noprofile --norc; zsh -f is enough, and both read
            // a plain `source` line fine. An unreadable index is a fact.
            if !err.is_empty() && out.is_empty() {
                println!("{} index: not readable — {}", label, err.chars().take(80).collect::<String>());
                best = None;
                break;
            }
            best = Some(best.map_or(took, |b: f64| b.min(took)));
        }
        if let Some(best) = best {
            let size = std::fs::metadata(path).map(|m| m.len() / 1024).unwrap_or(0);
            println!("{} index source: {:.0}ms ({}kB, per shell start)", label, best, size);
        }
    }
}

fn util_which(name: &str) -> Option<String> {
    crate::util::which(name)
}

pub fn doctor() {
    let broken = store::schema_note();
    if !broken.is_empty() {
        // First, because "rows=0" is what every reader reports for a broken
        // store as well as for an empty one, and the two need opposite advice.
        println!("tai: {} — every command is being read as zero rows", broken);
    }
    let (n, d) = store::count();
    let z = paths::index_location();
    println!("db: {} rows={} distinct={}", paths::db_location(), n, d);
    println!(
        "embedded indexes: zsh={} bash={}",
        std::path::Path::new(&z).exists(),
        std::path::Path::new(&paths::bash_index_location()).exists()
    );
    if !std::path::Path::new(&z).exists() {
        println!("run: tai refresh");
    }
    report_stale(d);
    let t0 = std::time::Instant::now();
    let r = predictor::suggest("docker ", "", "", "", &[], 1, None);
    println!(
        "suggest e2e={:.1}ms -> {:?}",
        t0.elapsed().as_secs_f64() * 1000.0,
        r.get("choice").and_then(|c| c.as_str()).unwrap_or("")
    );
}

fn report_stale(distinct: i64) {
    if !paths::enabled() {
        println!("path check: disabled by TAI_SKIP_PATH_CHECK=1");
        return;
    }
    let rows = match store::load_rows(20000) {
        Ok(r) => r,
        Err(e) => {
            println!("stale paths: not checked ({})", e);
            return;
        }
    };
    let mut eng = crate::engine::Engine::new();
    eng.build_from_rows(&rows);
    let cwd = std::env::current_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    let mut stale = predictor::LazyStale::new(&eng, &cwd);
    stale.materialize(&eng);
    let names: Vec<String> = eng
        .cmds
        .keys()
        .filter(|c| stale.ask(c))
        .cloned()
        .collect();
    if names.is_empty() {
        println!("stale paths: none");
        return;
    }
    let mut sorted = names.clone();
    sorted.sort();
    let sample: Vec<String> = sorted.iter().take(3).map(|c| preview(c)).collect();
    println!(
        "stale paths: {} of {} commands are not suggested ({}) — run 'tai purge --stale' to drop them",
        names.len(),
        distinct,
        sample.join(" ")
    );
    if names.len() as i64 >= distinct {
        // All of them. The index builder refuses in this state rather than
        // replacing what the user learned with the seed corpus.
        println!("…which is all of them: nothing is indexable until those are dropped");
    }
}

fn preview(cmd: &str) -> String {
    /// One bounded line for a command, whatever it is.
    let flat = cmd.split_whitespace().collect::<Vec<_>>().join(" ");
    let flat = if flat.chars().count() > 40 {
        let cut: String = flat.chars().take(39).collect();
        let cut = cut.trim_end().to_string();
        format!("{}…", cut)
    } else {
        flat
    };
    format!("\"{}\"", flat)
}
