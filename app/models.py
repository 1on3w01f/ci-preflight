from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
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
