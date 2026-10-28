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

## Compliance

LangSmith is a third-party processor: confirm its data region and retention
settings before enabling tracing in production.

## a) Portal session

Every call runs on whichever staff member's JWT is in the cookie/store, with no
refresh. Recommend: per-clinic service account plus token refresh.

## b) Telephony

sip.js in the browser bridges the call audio, so each call needs an open tab
and dies if the tab sleeps or closes. Compare moving the call leg server-side
(FreeSWITCH/Telnyx media stream to the backend) with keeping the browser.

## c) Compliance

Transcripts and logs contain patient data. Define retention, a call-recording
notice in the greeting, access control and an audit log. Requirements depend on
jurisdiction (India's DPDP Act, or HIPAA for US patients); list questions to
confirm. LangSmith is a third-party processor, so its data region and retention
settings need to be confirmed.
