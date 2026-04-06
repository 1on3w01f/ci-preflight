from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Installation(Base):
    __tablename__ = "installations"

    id = Column(Integer, primary_key=True)          # GitHub installation ID
    account_login = Column(String, nullable=False)  # username or org name
    account_type = Column(String, nullable=False)   # "User" or "Organization"
    installed_at = Column(DateTime, default=datetime.utcnow)

    repositories = relationship(
        "Repository",
        back_populates="installation",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Installation id={self.id} account={self.account_login}>"


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True)          # GitHub repo ID
    full_name = Column(String, nullable=False)       # "owner/repo"
    installation_id = Column(Integer, ForeignKey("installations.id"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

    installation = relationship("Installation", back_populates="repositories")

    def __repr__(self):
        return f"<Repository {self.full_name}>"


class PredictionRecord(Base):
    """
    Every prediction CI Preflight makes on a PR, persisted for accuracy tracking.
    Joined to CIOutcome on (repo_full_name, head_sha) to produce labeled training data.
    """
    __tablename__ = "prediction_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    installation_id = Column(Integer, nullable=False)
    repo_full_name = Column(String, nullable=False)
    pr_number = Column(Integer, nullable=False)
    head_sha = Column(String, nullable=False)
    check_type = Column(String, nullable=False)    # e.g. "dependency_lock_contract"
    failure_type = Column(String, nullable=False)  # e.g. "dependency_resolution_failure"
    confidence = Column(Float, nullable=False)
    severity = Column(String, nullable=False)      # HIGH / MEDIUM / LOW
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<PredictionRecord {self.check_type} {self.severity} {self.repo_full_name}#{self.pr_number}>"


class CIOutcome(Base):
    """
    The actual CI result for a given commit (head_sha).
    Captured from check_suite.completed webhooks posted by other CI systems.
    Joined to PredictionRecord to label predictions as true/false positives.
    """
    __tablename__ = "ci_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_full_name = Column(String, nullable=False)
    head_sha = Column(String, nullable=False)
    conclusion = Column(String, nullable=False)    # success / failure / cancelled / timed_out
    ci_app_name = Column(String, nullable=True)    # e.g. "GitHub Actions"
    recorded_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CIOutcome {self.repo_full_name} {self.head_sha[:7]} → {self.conclusion}>"
