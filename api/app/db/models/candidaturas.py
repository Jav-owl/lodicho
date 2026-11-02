from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import EstadoCandidatura, EstadoPlanCandidatura


class Candidatura(Base):
    """El plan de trabajo pertenece a la candidatura, no a la persona."""

    __tablename__ = "candidaturas"

    id: Mapped[int] = mapped_column(primary_key=True)
    organizacion_politica: Mapped[str] = mapped_column(String, nullable=False)
    lista_numero: Mapped[str] = mapped_column(String, nullable=False)
    dignidad: Mapped[str] = mapped_column(String, nullable=False)
    jurisdiccion_dpa: Mapped[str] = mapped_column(String, nullable=False, index=True)
    periodo: Mapped[str] = mapped_column(String, nullable=False)
    doc_id_plan: Mapped[str | None] = mapped_column(String, nullable=True)
    estado_plan: Mapped[EstadoPlanCandidatura] = mapped_column(
        SAEnum(EstadoPlanCandidatura, name="estado_plan_candidatura"),
        nullable=False,
        index=True,
    )
    estado_candidatura: Mapped[EstadoCandidatura] = mapped_column(
        SAEnum(EstadoCandidatura, name="estado_candidatura"),
        nullable=False,
        default=EstadoCandidatura.inscrito,
        server_default=EstadoCandidatura.inscrito.value,
        index=True,
    )

    candidatos: Mapped[list["Candidato"]] = relationship(
        "Candidato", back_populates="candidatura", cascade="all, delete-orphan"
    )


class Candidato(Base):
    """Una persona en una lista. Varios candidatos comparten una candidatura."""

    __tablename__ = "candidatos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    candidatura_id: Mapped[int] = mapped_column(
        ForeignKey("candidaturas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    posicion_lista: Mapped[int] = mapped_column(nullable=False)

    candidatura: Mapped["Candidatura"] = relationship(
        "Candidatura", back_populates="candidatos"
    )
