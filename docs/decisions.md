# Decisions

## Portal timezone contract

Two things need to be confirmed with the portal team before tightening
timezone handling:

1. Whether `appointmentBook` accepts timezone offsets in `startTime`/`endTime`
   (currently we send naive clinic-local ISO strings).
2. Whether `getAppointmentSlots` returns clinic-local or UTC times (the
   `[11:16]` slicing of the `open` field assumes clinic-local).

Until confirmed, `datetime.now()` checks use `settings.clinic_timezone` and
slot times are treated as clinic-local.
