import dagster as dg

from gtfs_zone_timetable_sites.pipeline import definitions

SITE_PAGES = dg.AssetKey("site_pages")


def test_shards_run_once_after_published_artifacts() -> None:
    instance = dg.DagsterInstance.ephemeral()
    for key in definitions.KEYS:
        instance.report_runless_asset_event(
            dg.AssetMaterialization(SITE_PAGES, partition=key)
        )
    result = dg.evaluate_automation_conditions(definitions.defs, instance)
    assert result.total_requested == 0

    instance.report_runless_asset_event(
        dg.AssetMaterialization(definitions.PUBLISHED_ARTIFACTS)
    )
    result = dg.evaluate_automation_conditions(
        definitions.defs, instance, cursor=result.cursor
    )
    assert result.get_requested_partitions(SITE_PAGES) == set(definitions.KEYS)

    result = dg.evaluate_automation_conditions(
        definitions.defs, instance, cursor=result.cursor
    )
    assert result.total_requested == 0


def test_after_catalog_runs_are_heavy() -> None:
    assert definitions.after_catalog.run_tags == {"gtfs.zone/memory": "heavy"}
    assert not list(definitions.defs.schedules or [])
