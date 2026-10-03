//! Per-turn watchdog and repeated no-progress write detection.

use serde_json::Value;
use std::collections::HashMap;
use std::time::Duration;

#[derive(Clone, Copy, Debug)]
pub struct WatchdogPolicy {
    pub total_limit: Duration,
    pub idle_limit: Duration,
    pub cancel_grace: Duration,
    pub repeated_write_limit: usize,
}

impl Default for WatchdogPolicy {
    fn default() -> Self {
        Self {
            total_limit: Duration::from_secs(60 * 60),
            idle_limit: Duration::from_secs(10 * 60),
            cancel_grace: Duration::from_secs(15),
            repeated_write_limit: 5,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Deadline {
    Total,
    Idle,
}

#[derive(Debug)]
pub struct ProgressClock {
    policy: WatchdogPolicy,
    last_progress: Duration,
}

impl ProgressClock {
    pub fn new(policy: WatchdogPolicy) -> Self {
        Self {
            policy,
            last_progress: Duration::ZERO,
        }
    }

    pub fn record_progress(&mut self, elapsed: Duration) {
        self.last_progress = elapsed;
    }

    pub fn expired(&self, elapsed: Duration) -> Option<Deadline> {
        if elapsed >= self.policy.total_limit {
            Some(Deadline::Total)
        } else if elapsed.saturating_sub(self.last_progress) >= self.policy.idle_limit {
            Some(Deadline::Idle)
        } else {
            None
        }
    }
}

#[derive(Default)]
pub struct WriteNoProgressBreaker {
    attempts: HashMap<String, usize>,
    last_revision: Option<i64>,
    limit: usize,
}

impl WriteNoProgressBreaker {
    pub fn with_limit(limit: usize) -> Self {
        Self {
            limit,
            ..Self::default()
        }
    }

    /// Returns true when a repeated write/error cycle should stop the turn.
    pub fn observe(&mut self, row: &Value) -> bool {
        if !row["success"].is_boolean() {
            return false;
        }
        let tool = row["tool"].as_str().unwrap_or_default();
        if !is_write_tool(tool) {
            return false;
        }
        let before = row["revision_before"].as_i64();
        let after = row["revision_after"].as_i64();
        if let Some(revision) = after.or(before) {
            if self
                .last_revision
                .is_some_and(|previous| revision > previous)
            {
                self.attempts.clear();
            }
            self.last_revision = Some(revision);
        }
        let no_progress = if row["success"] == true {
            before.is_some() && before == after
        } else {
            row.pointer("/error/message")
                .and_then(Value::as_str)
                .is_some()
        };
        if !no_progress {
            return false;
        }
        let args = serde_json::to_string(&row["arguments"]).unwrap_or_default();
        let error = if row["success"] == true {
            String::new()
        } else {
            row.pointer("/error/message")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        };
        let key = format!("{tool}|{}|{}|{}", args, before.unwrap_or(i64::MIN), error);
        let count = self.attempts.entry(key).or_default();
        *count += 1;
        self.limit > 0 && *count >= self.limit
    }
}

fn is_write_tool(tool: &str) -> bool {
    [
        "cad_create_",
        "cad_add_",
        "cad_update_",
        "cad_remove_",
        "cad_set_",
        "cad_finish_",
        "cad_restore_",
        "cad_apply_",
    ]
    .iter()
    .any(|prefix| tool.starts_with(prefix))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn watchdog_uses_independent_total_and_idle_limits() {
        let policy = WatchdogPolicy {
            total_limit: Duration::from_secs(100),
            idle_limit: Duration::from_secs(10),
            cancel_grace: Duration::from_secs(2),
            repeated_write_limit: 3,
        };
        let mut clock = ProgressClock::new(policy);
        assert_eq!(clock.expired(Duration::from_secs(9)), None);
        clock.record_progress(Duration::from_secs(9));
        assert_eq!(clock.expired(Duration::from_secs(18)), None);
        assert_eq!(clock.expired(Duration::from_secs(19)), Some(Deadline::Idle));
        clock.record_progress(Duration::from_secs(90));
        assert_eq!(
            clock.expired(Duration::from_secs(100)),
            Some(Deadline::Total)
        );
    }

    #[test]
    fn only_identical_nonprogress_writes_trip_breaker() {
        let mut breaker = WriteNoProgressBreaker::with_limit(5);
        let noop = serde_json::json!({"tool":"cad_add_hole_array","arguments":{"feature_id":"body"},"success":true,"revision_before":4,"revision_after":4});
        assert!(!breaker.observe(&noop));
        assert!(!breaker.observe(&noop));
        assert!(!breaker.observe(&noop));
        assert!(!breaker.observe(&noop));
        assert!(breaker.observe(&noop));
        let readonly = serde_json::json!({"tool":"cad_get_state","arguments":{},"success":true,"revision_before":4,"revision_after":4});
        assert!(!breaker.observe(&readonly));
        let error = serde_json::json!({"tool":"cad_add_feature","arguments":{"id":"x"},"success":false,"error":{"message":"same failure"}});
        assert!(!breaker.observe(&error));
        assert!(!breaker.observe(&error));
        assert!(!breaker.observe(&error));
        assert!(!breaker.observe(&error));
        assert!(breaker.observe(&error));
    }
}
