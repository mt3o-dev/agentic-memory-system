<script>
  import { get, post } from '../api.js'

  let { nodeId, onclose, onresolved } = $props()

  const ACTIONS = [
    { value: 'still_valid', label: 'Still valid — clear the flag (false alarm)' },
    { value: 'superseded', label: 'Superseded — archive, a newer node replaces it' },
    { value: 'wrong', label: 'Wrong / obsolete — archive it' },
    { value: 'needs_correction', label: 'Needs correction — edit the text, then clear' },
    { value: 'defer', label: 'Not sure — keep flagged, record that I looked' },
  ]

  let data = $state(null)
  let loading = $state(false)
  let applying = $state(false)
  let error = $state('')

  let action = $state('defer')
  let newBody = $state('')
  let replacementId = $state('')
  let reason = $state('')
  let recompute = $state(true)
  let tier = $state('')

  async function load(id) {
    loading = true
    error = ''
    data = null
    try {
      data = await get(`/api/review/${id}/guidance`)
      action = data.guidance.recommended_action
      newBody = data.guidance.suggested_body ?? data.node.body
      replacementId = ''
      reason = ''
      recompute = ['still_valid', 'needs_correction'].includes(action)
      tier = ''
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }

  $effect(() => {
    load(nodeId)
  })

  async function apply() {
    if (tier === 'lifetime' && !window.confirm('Promote to LIFETIME tier? This is the permanent knowledge tier.'))
      return
    applying = true
    error = ''
    try {
      const result = await post(`/api/review/${nodeId}/resolve`, {
        action,
        reason,
        recommended_action: data.guidance.recommended_action,
        new_body: action === 'needs_correction' ? newBody : null,
        replacement_id: action === 'superseded' ? replacementId || null : null,
        recompute_trust: recompute,
        tier: tier || null,
        tier_confirmed: tier === 'lifetime',
      })
      if (result.next_id) onresolved?.(result.next_id)
      else onclose?.()
    } catch (e) {
      error = e.message
    } finally {
      applying = false
    }
  }
</script>

<div class="modal d-block" style="background: rgba(0,0,0,0.5)" tabindex="-1">
  <div class="modal-dialog modal-lg modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header py-2">
        <h6 class="modal-title">Guided review</h6>
        <button type="button" class="btn-close" onclick={() => onclose?.()} aria-label="close"></button>
      </div>
      <div class="modal-body">
        {#if error}<div class="alert alert-danger py-1">{error}</div>{/if}
        {#if loading}
          <div class="text-secondary py-4 text-center">
            <div class="spinner-border spinner-border-sm me-2"></div>
            asking evaluator…
          </div>
        {:else if data}
          <div class="d-flex gap-2 align-items-center mb-2 small">
            <code>{data.node.path}</code>
            <span class="badge text-bg-danger">severity {data.severity.toFixed(1)}</span>
            <span class="badge text-bg-secondary">rules: {data.rules_verdict}</span>
            {#if data.guidance.source === 'template'}
              <span class="badge text-bg-warning" title="Set ANTHROPIC_API_KEY to enable the LLM evaluator">
                template guidance — no API key
              </span>
            {/if}
          </div>

          <p class="small">{data.guidance.explanation}</p>
          <p class="fw-semibold">{data.guidance.question}</p>

          <div class="row g-2 mb-3">
            <div class="col-6">
              <div class="small text-secondary mb-1">flagged node</div>
              <div class="border rounded p-2 small" style="white-space: pre-wrap">{data.node.body}</div>
            </div>
            <div class="col-6">
              <div class="small text-secondary mb-1">
                contradicted by
                {#if data.dependents.length}
                  · blast radius: {data.dependents.length} dependent(s)
                {/if}
              </div>
              {#each data.contradictors as c (c.id)}
                <div class="border rounded p-2 small mb-1" style="white-space: pre-wrap"><code class="d-block mb-1">{c.path}</code>{c.body}</div>
              {:else}
                <div class="text-secondary small">no contradicting node recorded (CONTRADICTED event)</div>
              {/each}
            </div>
          </div>

          {#each ACTIONS as opt (opt.value)}
            <div class="form-check">
              <input
                class="form-check-input"
                type="radio"
                name="action"
                id="act-{opt.value}"
                value={opt.value}
                bind:group={action}
              />
              <label class="form-check-label small" for="act-{opt.value}">
                {opt.label}
                {#if opt.value === data.guidance.recommended_action}
                  <span class="badge text-bg-info ms-1">AI recommends</span>
                {/if}
              </label>
            </div>
          {/each}
          <div class="small text-secondary mt-1 mb-2">{data.guidance.recommended_reason}</div>

          {#if action === 'needs_correction'}
            <textarea class="form-control form-control-sm mb-2" rows="4" bind:value={newBody}
              placeholder="corrected text"></textarea>
          {/if}
          {#if action === 'superseded'}
            <input class="form-control form-control-sm mb-2" bind:value={replacementId}
              placeholder="replacing node id (optional — records lineage)" />
          {/if}

          <input class="form-control form-control-sm mb-2" bind:value={reason}
            placeholder="why? (recorded in the journal)" />

          <div class="d-flex gap-3 align-items-center">
            <div class="form-check">
              <input class="form-check-input" type="checkbox" id="recompute" bind:checked={recompute} />
              <label class="form-check-label small" for="recompute">recompute trust after</label>
            </div>
            <select class="form-select form-select-sm w-auto" bind:value={tier}>
              <option value="">tier unchanged</option>
              <option value="short-term">short-term</option>
              <option value="mid-term">mid-term</option>
              <option value="long-term">long-term</option>
              <option value="lifetime">lifetime (confirm)</option>
            </select>
          </div>
        {/if}
      </div>
      <div class="modal-footer py-2">
        <button class="btn btn-sm btn-outline-secondary" onclick={() => onclose?.()}>close</button>
        <button class="btn btn-sm btn-primary" onclick={apply} disabled={loading || applying || !data}>
          {applying ? 'applying…' : 'apply decision'}
        </button>
      </div>
    </div>
  </div>
</div>
