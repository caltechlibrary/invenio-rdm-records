# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 CERN.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""CSL based Schema for Invenio RDM Records."""

import re

from edtf import parse_edtf
from edtf.parser.edtf_exceptions import EDTFParseException
from edtf.parser.parser_classes import Date, Interval
from flask_resources.serializers import BaseSerializerSchema
from invenio_access.permissions import system_identity
from invenio_records_resources.proxies import current_service_registry
from invenio_vocabularies.proxies import current_service as vocabulary_service
from marshmallow import Schema, fields, missing, pre_dump
from marshmallow_utils.fields import SanitizedUnicode, StrippedHTML

from ..utils import get_preferred_identifier


class CSLCreatorSchema(Schema):
    """Creator/contributor common schema."""

    family = fields.Str(attribute="person_or_org.family_name")
    given = fields.Str(attribute="person_or_org.given_name")

    @pre_dump
    def update_names(self, data, **kwargs):
        """Organizational creators do not have family/given name."""
        # family is required by CSL
        if not data.get("person_or_org").get("family_name"):
            name = data["person_or_org"]["name"]
            data["person_or_org"]["family_name"] = name

        return data


def add_if_not_none(year, month, day):
    """Adds year, month a day to a list if each are not None."""
    _list = []
    _list.append(year) if year else None
    _list.append(month) if month else None
    _list.append(day) if day else None
    return _list


class CSLJSONSchema(BaseSerializerSchema):
    """CSL Marshmallow Schema."""

    id_ = SanitizedUnicode(data_key="id", attribute="id")
    type_ = fields.Method("get_type", data_key="type")
    title = SanitizedUnicode(attribute="metadata.title")
    abstract = StrippedHTML(attribute="metadata.description")
    author = fields.List(fields.Nested(CSLCreatorSchema), attribute="metadata.creators")
    issued = fields.Method("get_issued")
    language = fields.Method("get_language")
    version = SanitizedUnicode(attribute="metadata.version")
    note = fields.Method("get_note")
    doi = fields.Str(attribute="pids.doi.identifier", data_key="DOI")
    isbn = fields.Method("get_isbn", data_key="ISBN")
    issn = fields.Method("get_issn", data_key="ISSN")
    publisher = SanitizedUnicode(attribute="metadata.publisher")

    container_title = fields.Method("get_container_title")
    page = fields.Method("get_pages")
    volume = fields.Str(attribute="custom_fields.journal:journal.volume")
    issue = fields.Str(attribute="custom_fields.journal:journal.issue")
    publisher_place = fields.Str(attribute="custom_fields.imprint:imprint.place")

    event = fields.Method("get_event")
    event_place = fields.Str(
        attribute="custom_fields.meeting:meeting.place", dump_to="event-place"
    )

    def _read_resource_type(self, id_):
        """Retrieve resource type record using service."""
        rec = vocabulary_service.read(system_identity, ("resourcetypes", id_))
        return rec._record

    def get_type(self, obj):
        """Get resource type."""
        resource_type = obj["metadata"].get(
            "resource_type", {"id": "publication-article"}
        )

        resource_type_record = self._read_resource_type(resource_type["id"])
        props = resource_type_record["props"]
        return props.get("csl", "article")  # article is CSL "Other"

    def get_issued(self, obj):
        """Get issued dates."""
        try:
            parsed = parse_edtf(obj["metadata"].get("publication_date"))
        except EDTFParseException:
            return missing

        if isinstance(parsed, Date):
            parts = add_if_not_none(parsed.year, parsed.month, parsed.day)
            return {"date-parts": [parts]}
        elif isinstance(parsed, Interval):
            d1 = parsed.lower
            d2 = parsed.upper
            return {
                "date-parts": [
                    add_if_not_none(d1.year, d1.month, d1.day),
                    add_if_not_none(d2.year, d2.month, d2.day),
                ]
            }
        else:
            return missing

    def get_language(self, obj):
        """Get language."""
        metadata = obj["metadata"]
        languages = metadata.get("languages")

        return languages[0]["id"] if languages else missing

    def get_isbn(self, obj):
        """Get ISBN."""
        identifiers = obj["metadata"].get("identifiers", [])
        for identifier in identifiers:
            if identifier["scheme"] == "ISBN":
                return identifier["identifier"]

        return missing

    def get_issn(self, obj):
        """Get ISSN."""
        identifiers = obj["metadata"].get("identifiers", [])
        for identifier in identifiers:
            if identifier["scheme"] == "ISSN":
                return identifier["identifier"]

        return missing

    def get_note(self, obj):
        """Get note from funders."""
        funding = obj["metadata"].get("funding")
        if funding:
            funder = funding[0]["funder"]
            id_ = funder.get("id")
            if id_:
                funder_service = current_service_registry.get("funders")
                funder = funder_service.read(system_identity, id_).to_dict()

            note = f"Funding by {funder['name']}"
            identifiers = funder.get("identifiers", [])
            identifier = get_preferred_identifier(
                priority=("ror", "grid", "doi", "isni", "gnd"), identifiers=identifiers
            )

            if identifier:
                note += (
                    f" {identifier['scheme'].upper()} " f"{identifier['identifier']}."
                )
            return note

        return missing

    def get_event(self, obj):
        """Get event/meeting title and acronym."""
        m = obj["metadata"]
        meeting = m.get("meeting", {})
        if meeting:
            title = meeting.get("title")
            acronym = meeting.get("acronym")
            if title and acronym:
                return "{} ({})".format(title, acronym)
            elif title or acronym:
                return title or acronym
        return missing

    def get_journal_or_imprint(self, obj, key):
        """Get journal or imprint metadata."""
        m = obj["custom_fields"]
        journal = m.get("journal:journal", {}).get(key)
        imprint = m.get("imprint:imprint", {}).get(key)

        return journal or imprint or missing

    def get_container_title(self, obj):
        """Get container title."""
        return self.get_journal_or_imprint(obj, "title")

    def get_pages(self, obj):
        """Get pages."""
        # Remove multiple dashes between page numbers (eg. 12--15)
        pages = self.get_journal_or_imprint(obj, "pages")
        pages = re.sub("-+", "-", pages) if pages else pages
        return pages
