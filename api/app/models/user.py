"""SQLAlchemy models for the Bugzilla ``profiles`` table and related auth tables.

Column names match the existing MySQL schema defined in
``Bugzilla/DB/Schema.pm`` so the FastAPI app works against the live database.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class Profile(Base):
    """Maps to the ``profiles`` table — the canonical user record."""

    __tablename__ = "profiles"

    userid = Column(Integer, primary_key=True, autoincrement=True)
    login_name = Column(String(255), nullable=False, unique=True)
    cryptpassword = Column(String(128), nullable=True)
    realname = Column(String(255), nullable=False, server_default="")
    disabledtext = Column(Text, nullable=False, server_default="")
    disable_mail = Column(Boolean, nullable=False, server_default="0")
    mybugslink = Column(Boolean, nullable=False, server_default="1")
    extern_id = Column(String(64), nullable=True, unique=True)
    is_enabled = Column(Boolean, nullable=False, server_default="1")
    last_seen_date = Column(DateTime, nullable=True)

    group_memberships = relationship("UserGroupMap", back_populates="user", lazy="selectin")
    login_cookies = relationship("LoginCookie", back_populates="user", lazy="selectin")
    saved_searches = relationship("NamedQuery", back_populates="user", lazy="selectin")
    saved_reports = relationship("Report", back_populates="user", lazy="selectin")

    @property
    def login(self) -> str:
        return self.login_name

    @property
    def email(self) -> str:
        return self.login_name

    @property
    def name(self) -> str:
        return self.realname

    @property
    def email_enabled(self) -> bool:
        return not self.disable_mail


class Group(Base):
    """Maps to the ``groups`` table."""

    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=False)
    isbuggroup = Column(Boolean, nullable=False)
    userregexp = Column(String(255), nullable=False, server_default="")
    isactive = Column(Boolean, nullable=False, server_default="1")
    icon_url = Column(String(255), nullable=True)


class UserGroupMap(Base):
    """Maps to ``user_group_map`` — direct/derived/regexp group membership."""

    __tablename__ = "user_group_map"

    user_id = Column(
        Integer, ForeignKey("profiles.userid", ondelete="CASCADE"), primary_key=True
    )
    group_id = Column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    isbless = Column(Boolean, nullable=False, server_default="0", primary_key=True)
    grant_type = Column(Integer, nullable=False, server_default="0", primary_key=True)

    user = relationship("Profile", back_populates="group_memberships")
    group = relationship("Group", lazy="joined")


class LoginCookie(Base):
    """Maps to ``logincookies`` — active login sessions / tokens."""

    __tablename__ = "logincookies"

    cookie = Column(String(16), primary_key=True)
    userid = Column(
        Integer, ForeignKey("profiles.userid", ondelete="CASCADE"), nullable=False
    )
    ipaddr = Column(String(40), nullable=True)
    lastused = Column(DateTime, nullable=False)

    user = relationship("Profile", back_populates="login_cookies")


class Token(Base):
    """Maps to ``tokens`` — password/email change request tokens."""

    __tablename__ = "tokens"

    token = Column(String(16), primary_key=True)
    userid = Column(
        Integer, ForeignKey("profiles.userid", ondelete="CASCADE"), nullable=True
    )
    issuedate = Column(DateTime, nullable=False)
    tokentype = Column(String(16), nullable=False)
    eventdata = Column(String(255), nullable=True)


class NamedQuery(Base):
    """Maps to ``namedqueries`` — saved searches."""

    __tablename__ = "namedqueries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    userid = Column(
        Integer, ForeignKey("profiles.userid", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(64), nullable=False)
    query = Column(Text, nullable=False)

    user = relationship("Profile", back_populates="saved_searches")


class Report(Base):
    """Maps to ``reports`` — saved reports."""

    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("profiles.userid", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(64), nullable=False)
    query = Column(Text, nullable=False)

    user = relationship("Profile", back_populates="saved_reports")
