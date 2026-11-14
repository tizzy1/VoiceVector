# Traffic snapshot integration

Bhavin's platform calls Tudor's `World.import_live(snapshot)`; ownership of simulator
normalization, filtering and flight initialization stays with Tudor.

## Operator flow

In the controller's right sidebar, expand **Traffic snapshots**. The default
synthetic demo adds SBX901/SBX902 near WILLO without any provider request. Choose
VATSIM for one manual HTTPS GET of its v3 JSON feed. Neither mode replaces existing
traffic or sends anything to the source network. Duplicate callsigns are skipped.
After import, targets are simulated AI-pilot aircraft, not continuously tracked
network aircraft. Both screens receive the import result. Radar tags use SNAP;
flight strips and selected-aircraft details identify imported simulation and provenance.

The platform fetches asynchronously, with a 10-second overall deadline, a 16 MiB
response limit, no redirects, and at most one provider attempt per 15 seconds.
It accepts no custom URLs or client-supplied raw feeds. Provider failures leave
existing aircraft in place. Reset cancels an in-flight fetch and clears provenance.
Personal name/CID fields are dropped by Tudor's normalizer; raw provider JSON is
not persisted, broadcast, or committed. Complete skipped counts are accompanied
by at most 20 skipped-detail records in the wire status.

Tudor's importer rejects snapshots older than 10 minutes and targets older than
120 seconds; source snapshots over 60 seconds old are flagged stale. Provider
timestamps are required by the fetch boundary. Synthetic snapshots are labelled
synthetic, never as live VATSIM data.

## Provider policy review

Reviewed 14 November 2026 in response to data/VERIFY.md item 34:

- [VATSIM User Agreement v1.5](https://cdn.vatsim.net/policy-documents/User%20Agreement%20v1.5.pdf), particularly data use and simulation-only provisions.
- [Commercial Use Policy v2.0](https://cdn.vatsim.net/policy-documents/VATSIM%20Commercial%20Use%20Policy%20v2.0.pdf), sections 1 and 3. Section 3.5 addresses personal, non-commercial use without fees; commercial/financial-benefit uses have additional permission/licensing requirements.
- [Data Protection and Handling Policy v1.4](https://cdn.vatsim.net/policy-documents/Data%20Protection%20and%20Handling%20Policy%20v1.4.pdf), including data minimization and transparency.

Bhavin confirmed this use has no financial benefit. This integration is for the
stated non-commercial simulation use; this note does not claim endorsement or
grant provider permission. Reassess provider access/terms before changing use,
commercializing, redistributing snapshots, or enabling another provider.

## Shared contract change

Optional LiveImportStatus/capability fields on StateMsg; optional source/time/credit
fields on AircraftPublic; additive import_live control action. Python and browser
definitions were committed together. No forecast, trace or benchmark wire shapes
were changed by this task.
