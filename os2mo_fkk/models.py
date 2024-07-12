# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
import itertools

import structlog
from datetime import datetime
from datetime import time
from datetime import timedelta
from more_itertools import one
from more_itertools import only
from typing import Iterator
from uuid import UUID

from os2mo_fkk.autogenerated_graphql_client import (
    ClassCreateInput as MOClassCreateInput,
)
from os2mo_fkk.autogenerated_graphql_client import (
    ClassUpdateInput as MOClassUpdateInput,
)
from os2mo_fkk.autogenerated_graphql_client import (
    GetClassClassesObjects as MOGetClassClassesObjects,
)
from os2mo_fkk.autogenerated_graphql_client import ValidityInput as MOValidityInput
from os2mo_fkk.klassifikation.models import HasVirking as HasFKKVirkning
from os2mo_fkk.klassifikation.models import Klasse as FKKKlasse
from os2mo_fkk.util import NEGATIVE_INFINITY
from os2mo_fkk.util import POSITIVE_INFINITY
from os2mo_fkk.util import StrictBaseModel

logger = structlog.stdlib.get_logger()


class Validity(StrictBaseModel):
    start: datetime | None
    end: datetime | None


class ClassValidity(StrictBaseModel):
    """Intermediate, comparable Class model.

    Both FKK Klasser and MO GraphQL Classes will be converted to this model for
    comparison. Used to determine actual vs desired state in OS2mo. See
    fkk_klasse_to_class_validities() for an explanation of the conversion logic.
    """

    facet: UUID
    validity: Validity
    uuid: UUID
    user_key: str
    name: str
    parent: UUID | None


def fkk_klasse_to_class_validities(
    klasse: FKKKlasse, facet: UUID
) -> Iterator[ClassValidity]:
    """Splits FKK Klasse object into Class validities.

    FKK Klasse objects are temporal, meaning that a single object contains all of its
    temporal changes. MO objects, on the other hand, model temporal changes by multiple
    validity "states" for each object. The values for each state are static; temporal
    changes are expressed through multiple states, none of which overlap.

    To convert from an FKK Klasse to a MO Class, we must therefore "split" the Klasse
    at each point in time where there is a change in any of its attributes, and
    construct a MO Class "state" for that time period.
    """
    # The following logic is based on a similar function in OS2mo, which does the same
    # for LoRa objects (which, like FKK, expose objects using the OIO-standard):
    # https://git.magenta.dk/rammearkitektur/os2mo/-/blob/f7317cec9a128d66b61e16c0315b039c35ac9e32/backend/mora/lora.py#L807-881

    # Collect all start and end timestamps in the FKK Klasse
    validity_objects = itertools.chain(
        klasse.attribut_egenskab,
        klasse.tilstand_publiceret,
        klasse.relation_overordnet,
    )
    timestamps = set()
    for obj in validity_objects:
        timestamps.add(obj.virkning.fra)
        timestamps.add(obj.virkning.til)

    def filter_virkning(
        objects: list[HasFKKVirkning], start: datetime, end: datetime
    ) -> Iterator[HasFKKVirkning]:
        return (
            obj
            for obj in objects
            if obj.virkning.fra < end and obj.virkning.til > start
        )

    # Construct MO Class validity state for each timestamp pair
    for start, end in itertools.pairwise(sorted(timestamps)):
        # The published state of an FKK Klasse designates whether the object is
        # considered "valid" according to the business logic in the interval. There is
        # no guarantee that the constructed MO Class validity will be consistent (i.e.
        # static in all values) during unpublished time periods, so we skip
        # constructing MO Class validities for these periods.
        published = one(filter_virkning(klasse.tilstand_publiceret, start, end))
        if not published.er_publiceret:
            continue

        attribute = one(filter_virkning(klasse.attribut_egenskab, start, end))
        parent = only(filter_virkning(klasse.relation_overordnet, start, end))

        # MO, and the intermediate ClassValidity model, uses None for infinity
        validity_start = None if start is NEGATIVE_INFINITY else start
        validity_end = None if end is POSITIVE_INFINITY else end

        yield ClassValidity(
            facet=facet,
            validity=Validity(
                start=validity_start,
                end=validity_end,
            ),
            uuid=klasse.uuid,
            user_key=attribute.brugervendtnoegle,
            name=attribute.titel,
            parent=parent.uuid if parent is not None else None,
        )


def mo_class_read_to_class_validities(
    mo_class: MOGetClassClassesObjects,
) -> Iterator[ClassValidity]:
    """Convert MO GraphQL Class object to ClassValidity intermediate objects."""
    for validity in mo_class.validities:
        # TODO (#61001): MOs GraphQL can return validities of zero length, i.e.
        # validities with intervals where from == to. This should *DEFINITELY* be fixed
        # in MO, but for now we just pretend such objects don't exist to avoid infinite
        # loops, since truncating the class does not remove these phantom validities.
        if validity.validity.from_ == validity.validity.to:  # pragma: no cover
            logger.warning("Ignoring zero-length validity", validity=validity)
            continue

        # TODO (#61435): MOs GraphQL subtracts one day from the validity end dates when
        # reading, compared to what was written. This breaks the comparison and leads
        # to infinite synchronisation loops.
        validity_end = validity.validity.to
        if validity_end is not None:
            assert validity_end.time() == time.min
            validity_end += timedelta(days=1)

        yield ClassValidity(
            facet=validity.facet_uuid,
            validity=Validity(
                start=validity.validity.from_,
                end=validity_end,
            ),
            uuid=validity.uuid,
            user_key=validity.user_key,
            name=validity.name,
            parent=validity.parent_uuid,
        )


def class_validity_to_create_input(class_validity: ClassValidity) -> MOClassCreateInput:
    """Convert ClassValidity intermediate object to MO GraphQL class_create input."""
    return MOClassCreateInput(
        facet_uuid=class_validity.facet,
        validity=MOValidityInput(
            from_=class_validity.validity.start,
            to=class_validity.validity.end,
        ),
        uuid=class_validity.uuid,
        user_key=class_validity.user_key,
        name=class_validity.name,
        parent_uuid=class_validity.parent,
    )


def class_validity_to_update_input(class_validity: ClassValidity) -> MOClassUpdateInput:
    """Convert ClassValidity intermediate object to MO GraphQL class_update input."""
    return MOClassUpdateInput(
        facet_uuid=class_validity.facet,
        validity=MOValidityInput(
            from_=class_validity.validity.start,
            to=class_validity.validity.end,
        ),
        uuid=class_validity.uuid,
        user_key=class_validity.user_key,
        name=class_validity.name,
        parent_uuid=class_validity.parent,
    )
