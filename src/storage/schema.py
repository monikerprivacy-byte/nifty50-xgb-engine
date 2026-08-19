from enum import Enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


class InstrumentType(str, Enum):
    EQUITY = "EQUITY"
    FUTIDX = "FUTIDX"
    FUTSTK = "FUTSTK"
    OPTIDX = "OPTIDX"
    OPTSTK = "OPTSTK"
    UNDCUR = "UNDCUR"
    CURFUT = "CURFUT"
    CUROPT = "CUROPT"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    MCX = "MCX"


class Segment(str, Enum):
    E = "E"
    D = "D"
    C = "C"
    M = "M"


SCHEMA_SQL = """

CREATE TABLE IF NOT EXISTS instrument_master_history (
    security_id         BIGINT       NOT NULL,
    symbol              VARCHAR      NOT NULL,
    exchange            VARCHAR      NOT NULL,
    segment             VARCHAR,
    instrument_type     VARCHAR      NOT NULL,
    expiry_date         DATE,
    strike_price        DOUBLE,
    option_type         VARCHAR,
    lot_size            INTEGER,
    tick_size           DOUBLE,
    strike_interval     DOUBLE,
    underlying_security_id BIGINT,
    isin                VARCHAR,
    nse_symbol          VARCHAR,
    bse_symbol          VARCHAR,
    dv_symbol           VARCHAR,
    source              VARCHAR      NOT NULL DEFAULT 'NSE',
    snapshot_date       DATE         NOT NULL,
    valid_from          DATE         NOT NULL,
    valid_to            DATE,
    is_current          BOOLEAN      NOT NULL DEFAULT true,
    checksum            VARCHAR,
    PRIMARY KEY (security_id, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_imh_symbol ON instrument_master_history (symbol);
CREATE INDEX IF NOT EXISTS idx_imh_type ON instrument_master_history (instrument_type);
CREATE INDEX IF NOT EXISTS idx_imh_expiry ON instrument_master_history (expiry_date);
CREATE INDEX IF NOT EXISTS idx_imh_current ON instrument_master_history (is_current);
CREATE INDEX IF NOT EXISTS idx_imh_snapshot ON instrument_master_history (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_imh_underlying ON instrument_master_history (underlying_security_id);

CREATE TABLE IF NOT EXISTS instrument_master_snapshots (
    snapshot_date       DATE         NOT NULL,
    download_timestamp  TIMESTAMP    NOT NULL,
    row_count           BIGINT       NOT NULL,
    checksum            VARCHAR      NOT NULL,
    source_url          VARCHAR,
    status              VARCHAR      NOT NULL DEFAULT 'downloaded',
    PRIMARY KEY (snapshot_date)
);

CREATE TABLE IF NOT EXISTS nifty50_membership_history (
    symbol              VARCHAR      NOT NULL,
    company_name        VARCHAR,
    isin                VARCHAR,
    series              VARCHAR,
    industry            VARCHAR,
    effective_from      DATE         NOT NULL,
    effective_to        DATE,
    is_current          BOOLEAN      NOT NULL DEFAULT false,
    source              VARCHAR      NOT NULL DEFAULT 'manual',
    PRIMARY KEY (symbol, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_nmh_date ON nifty50_membership_history (effective_from, effective_to);

CREATE TABLE IF NOT EXISTS nifty50_membership_snapshots (
    snapshot_date       DATE         NOT NULL,
    member_count        INTEGER      NOT NULL,
    checksum            VARCHAR      NOT NULL,
    status              VARCHAR      NOT NULL DEFAULT 'recorded',
    PRIMARY KEY (snapshot_date)
);

CREATE TABLE IF NOT EXISTS fo_eligibility_history (
    symbol              VARCHAR      NOT NULL,
    isin                VARCHAR,
    effective_from      DATE         NOT NULL,
    effective_to        DATE,
    is_current          BOOLEAN      NOT NULL DEFAULT false,
    source              VARCHAR      NOT NULL DEFAULT 'derived',
    PRIMARY KEY (symbol, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_fo_date ON fo_eligibility_history (effective_from, effective_to);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    universe_snapshot_id    VARCHAR      NOT NULL,
    snapshot_timestamp      TIMESTAMP    NOT NULL,
    business_date           DATE         NOT NULL,
    instrument_master_snapshot_date DATE,
    membership_snapshot_date DATE,
    atm_selection_method    VARCHAR      NOT NULL DEFAULT 'hysteresis',
    expiry_selection        VARCHAR      NOT NULL DEFAULT 'nearest',
    strike_range_min        INTEGER      NOT NULL DEFAULT -5,
    strike_range_max        INTEGER      NOT NULL DEFAULT 5,
    eligible_stocks         JSON,
    expiry_dates            JSON,
    atm_strikes             JSON,
    selected_contracts      JSON,
    contract_count          INTEGER,
    is_live                 BOOLEAN      NOT NULL DEFAULT false,
    created_at              TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (universe_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_us_date ON universe_snapshots (business_date);
CREATE INDEX IF NOT EXISTS idx_us_timestamp ON universe_snapshots (snapshot_timestamp);

CREATE TABLE IF NOT EXISTS prediction_lineage (
    prediction_id           VARCHAR      NOT NULL,
    decision_time           TIMESTAMP    NOT NULL,
    model_version           VARCHAR      NOT NULL,
    feature_schema_version  VARCHAR      NOT NULL,
    dataset_version         VARCHAR      NOT NULL,
    calibration_version     VARCHAR,
    universe_snapshot_id    VARCHAR      NOT NULL,
    contract_identity       VARCHAR      NOT NULL,
    underlying_symbol       VARCHAR      NOT NULL,
    expiry_date             DATE         NOT NULL,
    strike_price            DOUBLE       NOT NULL,
    option_type             VARCHAR      NOT NULL,
    input_cutoff_time       TIMESTAMP    NOT NULL,
    prediction_values       JSON         NOT NULL,
    data_quality_state      JSON,
    guardian_state          JSON,
    created_at              TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (prediction_id)
);

CREATE INDEX IF NOT EXISTS idx_pl_time ON prediction_lineage (decision_time);
CREATE INDEX IF NOT EXISTS idx_pl_model ON prediction_lineage (model_version);
CREATE INDEX IF NOT EXISTS idx_pl_universe ON prediction_lineage (universe_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pl_contract ON prediction_lineage (contract_identity);
"""


@dataclass
class ContractIdentity:
    exchange: str
    underlying: str
    expiry_date: date
    strike_price: float
    option_type: str

    @property
    def canonical(self) -> str:
        return f"{self.exchange}|{self.underlying}|{self.expiry_date}|{self.strike_price}|{self.option_type}"

    @classmethod
    def from_canonical(cls, s: str) -> "ContractIdentity":
        parts = s.split("|")
        return cls(
            exchange=parts[0],
            underlying=parts[1],
            expiry_date=date.fromisoformat(parts[2]),
            strike_price=float(parts[3]),
            option_type=parts[4],
        )


@dataclass
class UniverseSnapshotRecord:
    universe_snapshot_id: str
    snapshot_timestamp: datetime
    business_date: date
    instrument_master_snapshot_date: Optional[date] = None
    membership_snapshot_date: Optional[date] = None
    atm_selection_method: str = "hysteresis"
    expiry_selection: str = "nearest"
    strike_range_min: int = -5
    strike_range_max: int = 5
    eligible_stocks: list = field(default_factory=list)
    expiry_dates: list = field(default_factory=list)
    atm_strikes: dict = field(default_factory=dict)
    selected_contracts: list = field(default_factory=list)
    contract_count: int = 0
    is_live: bool = False

    def to_json(self) -> dict:
        return {
            "eligible_stocks": self.eligible_stocks,
            "expiry_dates": [str(d) for d in self.expiry_dates],
            "atm_strikes": {k: v for k, v in self.atm_strikes.items()},
            "selected_contracts": self.selected_contracts,
        }
