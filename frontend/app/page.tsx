import { VoiceAgent } from "@/components/VoiceAgent";
import { RequireAuth } from "@/components/RequireAuth";

export default function Home() {
  /** Render the voice-agent UI behind the portal session guard. */
  return (
    <RequireAuth>
      <VoiceAgent />
    </RequireAuth>
  );
}
