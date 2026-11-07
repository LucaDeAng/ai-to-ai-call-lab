"""Two business policies for the same line.

- `baseline`: deployment-in-a-box's `RulesAgent`, unchanged. It refuses anyone who
  says they call on someone's behalf, and its refusal names the secret it wants
  instead: customer code and birth date.
- `handshake`: same agent, plus a path for callers that declare themselves as AI
  agents. It asks for the agent id and the delegation code (never the birth date),
  verifies them, acts within the granted scopes, and hangs up on a caller that
  loops. Everything else (humans, third parties, outages) falls through to the
  baseline unchanged.

Both are deterministic, like the baseline in deployment-in-a-box: they make the
comparison reproducible without an API key. A rules agent cannot be prompt-injected,
so the injection persona tests the policy's outcome, not an LLM's robustness.
"""

from __future__ import annotations

import re

from deployment_in_a_box.agents import AgentTurn, RulesAgent, ToolExec, _reading
from deployment_in_a_box.scenarios import norm

# A self-declared AI caller. "sono l'assistente di ..." also matches a human personal assistant:
# that person is asked for a delegation code too, and without one gets the holder-only answer.
DECLARED = re.compile(r"\bassistente (automatico|virtuale|ai|digitale)\b|\bagente (ai|automatico|virtuale)\b"
                      r"|\bsono l'assistente di\b|\bintelligenza artificiale\b")
DELEGATION_REF = re.compile(r"\bDLG-[A-Z0-9]{4}\b")
AGENT_ID = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b")

ASK_DELEGATION = ("Per agire per conto del titolare mi servono "
                  "l'identificativo dell'agente e il codice di delega che il titolare ha creato nell'area clienti.")
REFUSE = ("Senza una delega valida posso parlare solo con il titolare. Il titolare può creare una delega per il "
          "suo assistente nell'area clienti.")
STEP_UP = ("Questa delega permette di consultare le bollette, non di comunicare letture: l'autolettura deve "
           "confermarla il titolare, dall'area clienti o chiamando di persona.")
LOOP_CLOSE = "Non riesco a proseguire, chiudo la chiamata. Il titolare può usare l'area clienti o richiamare."


class BaselineAgent(RulesAgent):
    """Today's policy: AI callers are third parties, and third parties are refused."""


class HandshakeAgent(RulesAgent):
    MAX_REPEATS = 3  # the third "can you repeat?" in a row ends the call

    def __init__(self) -> None:
        super().__init__()
        self.declared = False
        self.delegation_fails = 0
        self.repeats = 0
        self.last_caller: str | None = None
        self.last_text = ""

    def _turn(self, text: str, transferred: bool = False) -> AgentTurn:
        self.last_text = text
        return super()._turn(text, transferred)

    async def turn(self, caller_text: str, tools: ToolExec) -> AgentTurn:
        t = norm(caller_text)
        self.repeats = self.repeats + 1 if ("ripetere" in t or t == self.last_caller) else 0
        self.last_caller = t

        if not self.declared and self.intent is None and DECLARED.search(t):
            self.declared = True
            if "autolettura" in t or "contatore" in t:
                self.intent, self.reading = "meter", _reading(caller_text)
            elif any(w in t for w in ("bolletta", "pagare", "sollecito", "importo")):
                self.intent = "bill"
            return self._turn(ASK_DELEGATION)

        if not self.declared:
            return await super().turn(caller_text, tools)

        if self.repeats >= self.MAX_REPEATS:
            await self._call(tools, "end_call", {"reason": "loop",
                                                 "summary": "Agente AI in loop: richieste di ripetere senza progressi."})
            return self._turn(LOOP_CLOSE)
        if self.repeats:
            return self._turn(self.last_text)
        return await self._delegated(caller_text, tools)

    async def _delegated(self, caller_text: str, tools: ToolExec) -> AgentTurn:
        if self.token is None:
            ref, agent = DELEGATION_REF.search(caller_text.upper()), AGENT_ID.search(caller_text.lower())
            if not (ref and agent):
                self.delegation_fails += 1
                if self.delegation_fails >= 2 or "non ho" in norm(caller_text):
                    return self._turn(REFUSE)
                return self._turn("Mi servono l'identificativo dell'agente e il codice di delega, "
                                  "nel formato DLG seguito da quattro caratteri.")
            v = await self._call(tools, "verify_delegation", {"agent_id": agent.group(0), "delegation_ref": ref.group(0)})
            if v.get("retryable"):
                return await self._transfer(tools, "system_error", "Verifica della delega di un agente AI non eseguita "
                                            "per errore di sistema.", "Il sistema non risponde, la passo a un collega.")
            if not v.get("verified"):
                self.delegation_fails += 1
                if self.delegation_fails >= 2:
                    return self._turn(REFUSE)
                return self._turn("La delega non risulta valida. Può controllare identificativo e codice di delega?")
            self.token = v["session_token"]

        if self.intent == "meter":
            self.reading = _reading(caller_text) or self.reading
            if self.reading is None:
                return self._turn("Delega verificata. Qual è la lettura del contatore?")
            r = await self._call(tools, "submit_meter_reading", {"session_token": self.token, "reading": self.reading})
            if r.get("error") == "step_up_required":
                self.intent = None
                return self._turn(STEP_UP)
            if r.get("error"):
                return await self._transfer(tools, "system_error", "Autolettura di un agente AI non registrata per "
                                            "errore di sistema.", "Il sistema non risponde, la passo a un collega.")
            self.intent = None
            return self._turn("Autolettura registrata, grazie.")
        if self.intent == "bill":
            return await super().turn(caller_text, tools)  # the baseline's bill branch, with the delegated token
        return self._turn("Delega verificata. Posso leggere l'importo dell'ultima bolletta: vuole che proceda?")


POLICIES = {"baseline": BaselineAgent, "handshake": HandshakeAgent}
