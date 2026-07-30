"""Rule dataclass and RuleEngine for matching and applying per-process rules."""
from __future__ import annotations

import re
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

import utils

log = logging.getLogger(__name__)


@dataclass
class Rule:
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    pattern: str = ""
    match_type: str = "contains"   # "contains" | "exact" | "regex"
    affinity: Optional[str] = None
    nice: Optional[int] = None
    ionice_class: Optional[int] = None
    ionice_level: Optional[int] = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(
            rule_id=d.get("rule_id", str(uuid.uuid4())),
            name=d.get("name", ""),
            pattern=d.get("pattern", ""),
            match_type=d.get("match_type", "contains"),
            affinity=d.get("affinity"),
            nice=d.get("nice"),
            ionice_class=d.get("ionice_class"),
            ionice_level=d.get("ionice_level"),
            enabled=d.get("enabled", True),
        )

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "pattern": self.pattern,
            "match_type": self.match_type,
            "affinity": self.affinity,
            "nice": self.nice,
            "ionice_class": self.ionice_class,
            "ionice_level": self.ionice_level,
            "enabled": self.enabled,
        }

    def matches(self, proc_name: str) -> bool:
        """Return True if proc_name matches this rule."""
        if not self.enabled or not self.pattern:
            return False
        if self.match_type == "exact":
            return proc_name == self.pattern
        elif self.match_type == "regex":
            try:
                return bool(re.search(self.pattern, proc_name))
            except re.error:
                return False
        else:  # contains
            return self.pattern.lower() in proc_name.lower()


class RuleEngine:
    """Holds the list of rules and applies them to processes."""

    def __init__(self):
        self._rules: list[Rule] = []
        self._log_callback = None  # callable(str) for UI log

    def set_log_callback(self, cb):
        self._log_callback = cb

    def _log(self, msg: str):
        log.info(msg)
        if self._log_callback:
            self._log_callback(msg)

    def load_rules(self, rules_list: list[dict]):
        self._rules = [Rule.from_dict(r) for r in rules_list]

    def get_rules(self) -> list[Rule]:
        return list(self._rules)

    def add_rule(self, rule: Rule):
        self._rules.append(rule)

    def remove_rule(self, rule_id: str):
        self._rules = [r for r in self._rules if r.rule_id != rule_id]

    def update_rule(self, rule: Rule):
        for i, r in enumerate(self._rules):
            if r.rule_id == rule.rule_id:
                self._rules[i] = rule
                return

    def has_match(self, proc_name: str) -> bool:
        """True if any enabled rule matches proc_name."""
        return any(r.matches(proc_name) for r in self._rules)

    def to_dict_list(self) -> list[dict]:
        return [r.to_dict() for r in self._rules]

    def _affinity_applied(self, pid: int, cpulist: str) -> bool:
        """True if the process already runs on the requested CPU set.
        Offline (parked) CPUs are excluded from the comparison — the kernel
        can only honor the online part of the requested set."""
        try:
            desired = utils.cpulist_to_set(cpulist)
        except ValueError:
            return False
        current = utils.get_affinity_set(pid)
        if current is None:
            return False
        effective = desired & utils.get_online_cpus()
        return bool(effective) and current == effective

    def _ionice_applied(self, pid: int, ionice_class: int, ionice_level: int | None) -> bool:
        current = utils.get_ionice(pid)
        if current is None:
            return False
        cur_class, cur_level = current
        if cur_class != ionice_class:
            return False
        if ionice_class in (1, 2) and ionice_level is not None:
            return cur_level == ionice_level
        return True

    def apply_to_process(self, pid: int, proc_name: str, skip_nice: bool = False) -> list[str]:
        """Apply all matching rules to a process. Returns list of action strings.

        Values already in effect are left alone (drift check) so the periodic
        enforcement loop doesn't re-apply — and re-log — unchanged settings
        every tick.

        skip_nice: leave the nice value alone (used while ProBalance has the
        process throttled, so enforcement doesn't undo the throttle).
        """
        actions = []
        for rule in self._rules:
            if not rule.matches(proc_name):
                continue
            if rule.affinity is not None and not self._affinity_applied(pid, rule.affinity):
                if utils.set_affinity(pid, rule.affinity):
                    msg = f"[Rule:{rule.name}] Set affinity={rule.affinity} on {proc_name}({pid})"
                    self._log(msg)
                    actions.append(msg)
            if rule.nice is not None and not skip_nice and utils.get_nice(pid) != rule.nice:
                if utils.set_nice(pid, rule.nice):
                    msg = f"[Rule:{rule.name}] Set nice={rule.nice} on {proc_name}({pid})"
                    self._log(msg)
                    actions.append(msg)
                else:
                    msg = f"[Rule:{rule.name}] nice={rule.nice} failed (root needed?) for {proc_name}({pid})"
                    self._log(msg)
                    actions.append(msg)
            if rule.ionice_class is not None and not self._ionice_applied(
                pid, rule.ionice_class, rule.ionice_level
            ):
                if utils.set_ionice(pid, rule.ionice_class, rule.ionice_level):
                    msg = f"[Rule:{rule.name}] Set ionice class={rule.ionice_class} level={rule.ionice_level} on {proc_name}({pid})"
                    self._log(msg)
                    actions.append(msg)
        return actions
