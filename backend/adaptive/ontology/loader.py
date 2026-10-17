"""UTF-8 YAML ontology 파일을 읽고 검증된 DomainOntology를 반환한다.

기본 파일에는 역사적 정의도 보존되며 버전별 활성 항목 선택은 계약 객체가 담당한다.
현재 v3에서 사용 중단한 개설 연도 정의도 파일에서 삭제하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .contracts import DomainOntology


def default_ontology_path() -> Path:
    return Path(__file__).with_name("course-domain.yaml")


def load_ontology(path: str | Path) -> DomainOntology:
    ontology_path = Path(path)
    with ontology_path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return DomainOntology.model_validate(data)


def load_default_ontology() -> DomainOntology:
    return load_ontology(default_ontology_path())
