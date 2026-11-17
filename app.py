"""Brewery batches as Pixeltable tables: batches, hydrometer readings, and a gravity-board query.

`Batches` holds one brew (style, original and final gravity, volume); computed columns derive ABV and attenuation
with expression UDFs. `Readings` is the fermenter log: each reading's gravity is temperature-corrected, converted
to degrees Plato and labelled with a fermentation stage. cli.py drives it, and also exercises the `pxt` CLI itself.

Layout: UDFs live in udfs.py; this file holds the table models and the query helpers.

    pxt schema update app.py brew
    python cli.py seed
    python cli.py board
"""

from __future__ import annotations

import pixeltable as pxt
import pixeltable.functions as pxtf

from udfs import abv_pct, attenuation_pct, plato, stage, temp_corrected_sg

TableModel = pxt.model_base()


class Batches(TableModel, name='batches'):
    """One brew."""

    batch_id = pxt.Column(type=pxt.String, primary_key=True)
    name: pxt.String
    style: pxt.String
    brewed_on: pxt.Date
    volume_l: pxt.Float
    og: pxt.Float
    fg: pxt.Float | None

    abv = abv_pct(og, fg)
    attenuation = attenuation_pct(og, fg)

    __indexes__ = [pxt.BtreeIndex(style)]


class Readings(TableModel, name='readings'):
    """One hydrometer reading of a batch in the fermenter, with the batch's OG copied on."""

    reading_id = pxt.Column(type=pxt.String, primary_key=True)
    batch_id: pxt.String
    taken_at: pxt.Timestamp
    gravity: pxt.Float
    temp_c: pxt.Float
    og: pxt.Float

    corrected = temp_corrected_sg(gravity, temp_c)
    degrees_plato = plato(corrected)
    phase = stage(corrected, og)

    __indexes__ = [pxt.BtreeIndex(batch_id)]


MODELS = (Batches, Readings)


# Query helpers: plain functions over the bound tables.
def gravity_board() -> pxt.Query:
    """Batches ranked by ABV (finished ones first)."""
    b = Batches.table
    return b.order_by(b.abv, asc=False).select(b.batch_id, b.name, b.style, b.og, b.fg, b.abv, b.attenuation)


def batch_curve(batch_id: str) -> pxt.Query:
    """One batch's readings, oldest first."""
    r = Readings.table
    return (
        r.where(r.batch_id == batch_id)
        .order_by(r.taken_at)
        .select(r.taken_at, r.gravity, r.temp_c, r.corrected, r.degrees_plato, r.phase)
    )


def readings_per_batch() -> pxt.Query:
    """Reading count and lowest corrected gravity per batch."""
    r = Readings.table
    return (
        r.group_by(r.batch_id)
        .select(r.batch_id, n=pxtf.count(r.reading_id), lowest=pxtf.min(r.corrected))
        .order_by(r.batch_id)
    )


def phase_counts(phase: str) -> pxt.Query:
    """Readings labelled with a fermentation stage."""
    r = Readings.table
    return r.where(r.phase == phase).order_by(r.batch_id, r.taken_at).select(r.batch_id, r.taken_at, r.corrected, r.phase)
