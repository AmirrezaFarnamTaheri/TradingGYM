/**
 * ForgePanel Component
 *
 * The agent's own memory, strategy grammar, and tool-nomination queue
 * (ADR-034). Three read/propose surfaces, no authority:
 *
 * - **Memory** shows the bounded advice digest for a tag set — warnings
 *   from losses and confirmations from wins, each with its support count,
 *   plus a net signal — and lets an operator append one observation. Raw
 *   journal rows are never fetched, so a large memory stays a small view.
 * - **Strategy grammar** lists the curated families and derives
 *   grammar-bounded variants of one. Proposal only: nothing is saved,
 *   scored, or applied here.
 * - **Nominations** is the X-tier queue. An agent can request a tool it
 *   lacks; only an operator can resolve one, and resolving does not add
 *   the tool — human absorption does (ADR-033 §1).
 *
 * The ADR-035 kernel badge reports whether the compiled simulation kernel
 * is loaded, because "which loop ran" must never be guessed from timing.
 *
 * @component
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { forgeAPI } from '../api';
import { useToast } from './Toast';

const DEFAULT_TAGS = 'regime:chop, symbol:btcusdt';

function parseTags(text) {
  return text
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function NetSignal({ value, consulted }) {
  if (consulted === 0) {
    return (
      <span className="net-signal neutral" data-testid="net-signal">
        no evidence
      </span>
    );
  }
  const tone = value < 0 ? 'negative' : value > 0 ? 'positive' : 'neutral';
  return (
    <span className={`net-signal ${tone}`} data-testid="net-signal">
      {value > 0 ? '+' : ''}
      {value.toFixed(2)}
    </span>
  );
}

function LessonList({ title, items, kind }) {
  if (!items || items.length === 0) return null;
  return (
    <div className={`lesson-group ${kind}`}>
      <h4>{title}</h4>
      <ul>
        {items.map((entry) => (
          <li key={`${kind}-${entry.lesson}`}>
            <span className="lesson-text">{entry.lesson}</span>
            <span className="support-badge" title="observations consolidated">
              ×{entry.support}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ForgePanel() {
  const [actor, setActor] = useState('');
  const [actorKey, setActorKey] = useState('');
  const [tagText, setTagText] = useState(DEFAULT_TAGS);
  const [queriedTags, setQueriedTags] = useState(null);
  const [lesson, setLesson] = useState('');
  const [outcome, setOutcome] = useState('loss');
  const [templateKey, setTemplateKey] = useState('');
  const [variantSeed, setVariantSeed] = useState(7);
  const [variantCount, setVariantCount] = useState(3);
  const [variants, setVariants] = useState(null);
  const [toolName, setToolName] = useState('');
  const [rationale, setRationale] = useState('');

  const queryClient = useQueryClient();
  const toast = useToast();
  const credentialed = Boolean(actor && actorKey);

  const kernelQuery = useQuery({
    queryKey: ['forge-kernel'],
    queryFn: () => forgeAPI.kernel().then((response) => response.data),
    refetchInterval: 60000,
  });

  const templatesQuery = useQuery({
    queryKey: ['forge-templates', actor],
    queryFn: () =>
      // detail=true: the full specs are what the variant call needs, and the
      // summary fields the selector shows are derived from them — one fetch,
      // one source of truth.
      forgeAPI.templates(actor, actorKey, true).then((response) => response.data),
    enabled: credentialed,
  });

  const nominationsQuery = useQuery({
    queryKey: ['forge-nominations', actor],
    queryFn: () =>
      forgeAPI.nominations(actor, actorKey).then((response) => response.data),
    enabled: credentialed,
    refetchInterval: 30000,
  });

  const adviceQuery = useQuery({
    queryKey: ['forge-advice', queriedTags, actor],
    queryFn: () =>
      forgeAPI
        .memoryAdvice(queriedTags, actor, actorKey)
        .then((response) => response.data),
    enabled: credentialed && Array.isArray(queriedTags) && queriedTags.length > 0,
  });

  const recordMutation = useMutation({
    mutationFn: (payload) => forgeAPI.recordExperience(payload, actor, actorKey),
    onSuccess: (response) => {
      toast.success(
        `Observation recorded (support ${response.data.support})`
      );
      setLesson('');
      queryClient.invalidateQueries({ queryKey: ['forge-advice'] });
    },
    onError: (error) => toast.error(error.message || 'Record failed'),
  });

  const variantMutation = useMutation({
    mutationFn: (payload) => forgeAPI.strategyVariants(payload, actor, actorKey),
    onSuccess: (response) => {
      setVariants(response.data.variants);
      toast.info(`${response.data.variants.length} variants proposed`);
    },
    onError: (error) => toast.error(error.message || 'Variant proposal failed'),
  });

  const nominateMutation = useMutation({
    mutationFn: (payload) => forgeAPI.nominate(payload, actor, actorKey),
    onSuccess: () => {
      toast.success('Nomination journaled for human absorption');
      setToolName('');
      setRationale('');
      queryClient.invalidateQueries({ queryKey: ['forge-nominations'] });
    },
    onError: (error) => toast.error(error.message || 'Nomination failed'),
  });

  const resolveMutation = useMutation({
    mutationFn: ({ name, decision }) =>
      forgeAPI.resolveNomination(name, { decision }, actor, actorKey),
    onSuccess: (response) => {
      toast.success(`Nomination ${response.data.status}`);
      queryClient.invalidateQueries({ queryKey: ['forge-nominations'] });
    },
    onError: (error) => toast.error(error.message || 'Resolution failed'),
  });

  const advice = adviceQuery.data;
  const templates = templatesQuery.data?.templates ?? {};
  const templateKeys = Object.keys(templates);
  const nominations = nominationsQuery.data?.nominations ?? [];
  const kernel = kernelQuery.data;

  return (
    <section className="panel forge-panel" aria-label="Forge and memory">
      <h2>Forge &amp; Memory</h2>
      <p className="panel-hint">
        The agent&apos;s two-horizon memory, grammar-bounded strategy
        development, and the X-tier tool queue (ADR-034). Everything here
        proposes; the ADR-033 gate chain decides. Simulation kernel:{' '}
        <strong data-testid="kernel-state">
          {kernel?.available ? 'Rust (ADR-035)' : 'Python reference'}
        </strong>
        .
      </p>

      <form
        className="credential-row"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Actor credentials"
      >
        <input
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          placeholder="X-Actor (e.g. agent-axiom1)"
          aria-label="Actor principal"
        />
        <input
          type="password"
          value={actorKey}
          onChange={(event) => setActorKey(event.target.value)}
          placeholder="X-Actor-Key"
          aria-label="Actor key"
        />
      </form>

      {!credentialed && (
        <p className="empty-state">
          Enter actor credentials to read memory, list templates, and view the
          nomination queue. Each surface is separately scope-gated.
        </p>
      )}

      <section className="forge-section" aria-label="Long-term memory">
        <h3>Long-term memory</h3>
        <form
          className="check-row"
          onSubmit={(event) => {
            event.preventDefault();
            setQueriedTags(parseTags(tagText));
          }}
          aria-label="Memory advice query"
        >
          <input
            value={tagText}
            onChange={(event) => setTagText(event.target.value)}
            placeholder="comma-separated tags"
            aria-label="Memory tags"
          />
          <button type="submit" disabled={!credentialed}>
            Consult
          </button>
          {advice && (
            <span className="advice-summary" data-testid="advice-summary">
              net <NetSignal value={advice.net_signal} consulted={advice.consulted} />
              {' · '}
              {advice.consulted} consulted of {advice.entries} lessons (
              {advice.observations} observations)
            </span>
          )}
        </form>

        {advice && advice.consulted === 0 && (
          <p className="empty-state" data-testid="advice-empty">
            Memory has seen nothing for these tags.
          </p>
        )}
        {advice && (
          <div className="advice-lists" data-testid="advice-lists">
            <LessonList
              title="Warnings"
              items={advice.warnings}
              kind="warning"
            />
            <LessonList
              title="Confirmations"
              items={advice.confirmations}
              kind="confirmation"
            />
          </div>
        )}

        <form
          className="record-form"
          onSubmit={(event) => {
            event.preventDefault();
            recordMutation.mutate({
              context_tags: parseTags(tagText),
              outcome,
              lesson,
            });
          }}
          aria-label="Record an observation"
        >
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
            aria-label="Outcome"
          >
            <option value="loss">loss</option>
            <option value="profitable">profitable</option>
            <option value="neutral">neutral</option>
          </select>
          <input
            value={lesson}
            onChange={(event) => setLesson(event.target.value)}
            placeholder="lesson (what the next decision should know)"
            aria-label="Lesson"
            maxLength={400}
          />
          <button
            type="submit"
            disabled={!credentialed || !lesson.trim() || recordMutation.isPending}
          >
            Record
          </button>
        </form>
      </section>

      <section className="forge-section" aria-label="Strategy grammar">
        <h3>Strategy development</h3>
        <p className="section-hint">
          Variants jitter indicator parameters and comparison constants inside
          the grammar. Structure is never mutated — that is an S-tier revision.
        </p>
        <form
          className="variant-form"
          onSubmit={(event) => {
            event.preventDefault();
            const spec = templates[templateKey];
            if (!spec) return;
            variantMutation.mutate({
              strategy: spec,
              count: Number(variantCount),
              seed: Number(variantSeed),
            });
          }}
          aria-label="Propose variants"
        >
          <select
            value={templateKey}
            onChange={(event) => setTemplateKey(event.target.value)}
            aria-label="Template family"
            disabled={templateKeys.length === 0}
          >
            <option value="">select a family…</option>
            {templateKeys.map((key) => (
              <option key={key} value={key}>
                {templates[key].name}
              </option>
            ))}
          </select>
          <label>
            Count
            <input
              type="number"
              min="1"
              max="16"
              value={variantCount}
              onChange={(event) => setVariantCount(event.target.value)}
              aria-label="Variant count"
            />
          </label>
          <label>
            Seed
            <input
              type="number"
              min="0"
              value={variantSeed}
              onChange={(event) => setVariantSeed(event.target.value)}
              aria-label="Variant seed"
            />
          </label>
          <button
            type="submit"
            disabled={!credentialed || !templateKey || variantMutation.isPending}
          >
            Propose
          </button>
        </form>

        {templateKey && templates[templateKey] && (
          <p className="template-detail" data-testid="template-detail">
            {templates[templateKey].description} —{' '}
            <code>{templates[templateKey].timeframe}</code>,{' '}
            {(templates[templateKey].indicators || [])
              .map((indicator) => indicator.name)
              .join(', ')}
          </p>
        )}

        {variants && (
          <table className="variants-table" data-testid="variants-table">
            <thead>
              <tr>
                <th scope="col">Variant</th>
                <th scope="col">Indicators</th>
              </tr>
            </thead>
            <tbody>
              {variants.map((variant) => (
                <tr key={variant.name}>
                  <td>
                    <code>{variant.name}</code>
                  </td>
                  <td>
                    {variant.indicators
                      .map(
                        (indicator) =>
                          `${indicator.name}(${Object.entries(indicator.params)
                            .map(([key, value]) => `${key}=${value}`)
                            .join(', ')})`
                      )
                      .join('  ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="forge-section" aria-label="Tool nominations">
        <h3>Tool nominations (X-tier)</h3>
        <p className="section-hint">
          Code is never self-revisable. A nomination is a request, not a grant:
          the tool stays uncallable until human absorption adds it.
        </p>
        <form
          className="nominate-form"
          onSubmit={(event) => {
            event.preventDefault();
            nominateMutation.mutate({
              tool_name: toolName,
              rationale,
            });
          }}
          aria-label="Nominate a tool"
        >
          <input
            value={toolName}
            onChange={(event) => setToolName(event.target.value)}
            placeholder="tool.name"
            aria-label="Tool name"
          />
          <input
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            placeholder="why the agent needs it"
            aria-label="Rationale"
          />
          <button
            type="submit"
            disabled={
              !credentialed ||
              !toolName.trim() ||
              !rationale.trim() ||
              nominateMutation.isPending
            }
          >
            Nominate
          </button>
        </form>

        {nominations.length === 0 ? (
          <p className="empty-state">No nominations in the queue.</p>
        ) : (
          <table className="nominations-table" data-testid="nominations-table">
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Proposed by</th>
                <th scope="col">Rationale</th>
                <th scope="col">Status</th>
                <th scope="col">Decision</th>
              </tr>
            </thead>
            <tbody>
              {nominations.map((entry) => (
                <tr key={entry.tool_name}>
                  <td>
                    <code>{entry.tool_name}</code>
                  </td>
                  <td>{entry.proposed_by}</td>
                  <td className="rationale-cell">{entry.rationale}</td>
                  <td>
                    <span className={`nomination-status ${entry.status}`}>
                      {entry.status}
                    </span>
                  </td>
                  <td>
                    {entry.status === 'nominated' ? (
                      <>
                        <button
                          type="button"
                          disabled={!credentialed || resolveMutation.isPending}
                          onClick={() =>
                            resolveMutation.mutate({
                              name: entry.tool_name,
                              decision: 'accepted',
                            })
                          }
                          aria-label={`Accept ${entry.tool_name}`}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          disabled={!credentialed || resolveMutation.isPending}
                          onClick={() =>
                            resolveMutation.mutate({
                              name: entry.tool_name,
                              decision: 'rejected',
                            })
                          }
                          aria-label={`Reject ${entry.tool_name}`}
                        >
                          Reject
                        </button>
                      </>
                    ) : (
                      <span className="decided-by">{entry.decided_by}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </section>
  );
}
