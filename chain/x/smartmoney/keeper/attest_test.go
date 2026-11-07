package keeper_test

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"testing"

	"github.com/cosmos/cosmos-sdk/types/query"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/keeper"
	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

var govAuthority = authtypes.NewModuleAddress("gov").String()

func proofHash(order, capture string) string {
	h := sha256.Sum256([]byte(order + "|" + capture))
	return hex.EncodeToString(h[:])
}

// setParams installs signers/threshold through MsgUpdateParams.
func (e *env) setParams(threshold uint32, signers ...string) {
	e.t.Helper()
	if _, err := e.srv.UpdateParams(e.ctx, &types.MsgUpdateParams{Authority: govAuthority,
		Params: types.Params{AnchorSigners: signers, AnchorThreshold: threshold}}); err != nil {
		e.t.Fatal(err)
	}
}

func (e *env) attest(signer, hash, order, capture string) (*types.MsgAnchorProofResponse, error) {
	return e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: signer, Hash: hash, ExternalRef: order, CaptureRef: capture})
}

func TestAttestationThresholdReached(t *testing.T) {
	e := setup(t)
	a2, a3 := addr("attestor2"), addr("attestor3")
	e.setParams(2, e.gate, a2, a3)
	hx := proofHash("O-1", "C-1")

	e.height(5)
	r, err := e.attest(e.gate, hx, "O-1", "C-1")
	if err != nil || r.Status != types.PROOF_STATUS_PENDING || r.Attestations != 1 {
		t.Fatal(r, err)
	}
	p, _ := e.k.Proofs.Get(e.ctx, hx)
	if p.Status != types.PROOF_STATUS_PENDING || p.ConfirmedHeight != 0 || p.FirstSeenHeight != 5 || p.Submitter != e.gate {
		t.Fatal(p)
	}
	e.height(7)
	r, err = e.attest(a2, hx, "O-1", "C-1")
	if err != nil || r.Status != types.PROOF_STATUS_CONFIRMED || r.Attestations != 2 {
		t.Fatal(r, err)
	}
	var confirmed bool
	for _, ev := range e.ctx.EventManager().Events() {
		if ev.Type == "proof_confirmed" {
			confirmed = true
		}
	}
	if !confirmed {
		t.Fatal("no proof_confirmed event")
	}
	p, _ = e.k.Proofs.Get(e.ctx, hx)
	if p.ConfirmedHeight != 7 || p.FirstSeenHeight != 5 || len(p.Attestations) != 2 {
		t.Fatal(p)
	}
	// late attestor on a CONFIRMED proof is appended, status unchanged
	e.height(9)
	r, err = e.attest(a3, hx, "O-1", "C-1")
	if err != nil || r.Status != types.PROOF_STATUS_CONFIRMED || r.Attestations != 3 {
		t.Fatal(r, err)
	}
	p, _ = e.k.Proofs.Get(e.ctx, hx)
	if p.ConfirmedHeight != 7 {
		t.Fatal("confirmed height moved", p)
	}
	e.invariants()
}

func TestThresholdOneConfirmsImmediately(t *testing.T) {
	e := setup(t) // default genesis: [gateway], threshold 1
	hx := proofHash("O-2", "C-2")
	r, err := e.attest(e.gate, hx, "O-2", "C-2")
	if err != nil || r.Status != types.PROOF_STATUS_CONFIRMED || r.Attestations != 1 {
		t.Fatal(r, err)
	}
	p, _ := e.k.Proofs.Get(e.ctx, hx)
	if p.ConfirmedHeight != 1 || p.CaptureRef != "C-2" {
		t.Fatal(p)
	}
}

func TestDuplicateAttestorRejected(t *testing.T) {
	e := setup(t)
	e.setParams(2, e.gate, addr("attestor2"))
	hx := proofHash("O", "C")
	if _, err := e.attest(e.gate, hx, "O", "C"); err != nil {
		t.Fatal(err)
	}
	if _, err := e.attest(e.gate, hx, "O", "C"); !errors.Is(err, types.ErrDuplicateAttestation) {
		t.Fatal(err)
	}
}

func TestUnauthorisedAttestor(t *testing.T) {
	e := setup(t)
	if _, err := e.attest(e.alice, proofHash("O", "C"), "O", "C"); !errors.Is(err, types.ErrUnauthorized) {
		t.Fatal(err)
	}
}

func TestMismatchedRefsRejected(t *testing.T) {
	e := setup(t)
	a2, a3 := addr("attestor2"), addr("attestor3")
	e.setParams(3, e.gate, a2, a3)
	hx := proofHash("O", "C")
	if _, err := e.attest(e.gate, hx, "O", "C"); err != nil {
		t.Fatal(err)
	}
	if _, err := e.attest(a2, hx, "O-other", "C"); !errors.Is(err, types.ErrProofMismatch) {
		t.Fatal("external_ref mismatch:", err)
	}
	if _, err := e.attest(a2, hx, "O", "C-other"); !errors.Is(err, types.ErrProofMismatch) {
		t.Fatal("capture_ref mismatch:", err)
	}
	p, _ := e.k.Proofs.Get(e.ctx, hx)
	if len(p.Attestations) != 1 {
		t.Fatal(p)
	}
}

func TestPendingProofsQuery(t *testing.T) {
	e := setup(t)
	e.setParams(2, e.gate, addr("attestor2"))
	q := keeper.NewQueryServerImpl(e.k)
	pend1, pend2, done := proofHash("A", "1"), proofHash("B", "2"), proofHash("C", "3")
	for _, h := range []string{pend1, pend2, done} {
		if _, err := e.attest(e.gate, h, "o", "c"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := e.attest(addr("attestor2"), done, "o", "c"); err != nil {
		t.Fatal(err)
	}
	res, err := q.PendingProofs(e.ctx, &types.QueryPendingProofsRequest{})
	if err != nil || len(res.Proofs) != 2 {
		t.Fatal(res, err)
	}
	for _, p := range res.Proofs {
		if p.Hash == done || p.Status != types.PROOF_STATUS_PENDING {
			t.Fatal(p)
		}
	}
	res, err = q.PendingProofs(e.ctx, &types.QueryPendingProofsRequest{Pagination: &query.PageRequest{Limit: 1, CountTotal: true}})
	if err != nil || len(res.Proofs) != 1 || res.Pagination.Total != 2 {
		t.Fatal(res, err)
	}
	if pr, err := q.Proof(e.ctx, &types.QueryProofRequest{Hash: done}); err != nil || pr.Proof.Status != types.PROOF_STATUS_CONFIRMED {
		t.Fatal(pr, err)
	}
}

func TestUpdateParamsAuthority(t *testing.T) {
	e := setup(t)
	if e.k.GetAuthority() != govAuthority {
		t.Fatal("authority")
	}
	good := types.Params{AnchorSigners: []string{e.gate, addr("attestor2")}, AnchorThreshold: 2}
	if _, err := e.srv.UpdateParams(e.ctx, &types.MsgUpdateParams{Authority: e.alice, Params: good}); !errors.Is(err, types.ErrInvalidAuthority) {
		t.Fatal(err)
	}
	bad := types.Params{AnchorSigners: []string{e.gate}, AnchorThreshold: 2}
	if _, err := e.srv.UpdateParams(e.ctx, &types.MsgUpdateParams{Authority: govAuthority, Params: bad}); !errors.Is(err, types.ErrInvalidParams) {
		t.Fatal(err)
	}
	if _, err := e.srv.UpdateParams(e.ctx, &types.MsgUpdateParams{Authority: govAuthority, Params: good}); err != nil {
		t.Fatal(err)
	}
	p, _ := e.k.Params.Get(e.ctx)
	if p.AnchorThreshold != 2 || len(p.AnchorSigners) != 2 {
		t.Fatal(p)
	}
}

func TestParamsAndGenesisValidation(t *testing.T) {
	g, g2 := addr("gateway"), addr("attestor2")
	cases := []struct {
		name string
		p    types.Params
		ok   bool
	}{
		{"default", types.DefaultParams(), true},
		{"empty threshold 0", types.Params{}, true},
		{"one signer t1", types.Params{AnchorSigners: []string{g}, AnchorThreshold: 1}, true},
		{"two signers t2", types.Params{AnchorSigners: []string{g, g2}, AnchorThreshold: 2}, true},
		{"threshold 0 with signer", types.Params{AnchorSigners: []string{g}, AnchorThreshold: 0}, false},
		{"threshold above n", types.Params{AnchorSigners: []string{g, g2}, AnchorThreshold: 3}, false},
		{"duplicate signer", types.Params{AnchorSigners: []string{g, g}, AnchorThreshold: 1}, false},
		{"bad address", types.Params{AnchorSigners: []string{"nope"}, AnchorThreshold: 1}, false},
	}
	for _, c := range cases {
		if err := c.p.Validate(); (err == nil) != c.ok {
			t.Errorf("params %s: %v", c.name, err)
		}
		gs := types.DefaultGenesis()
		gs.Params = c.p
		if err := gs.Validate(); (err == nil) != c.ok {
			t.Errorf("genesis %s: %v", c.name, err)
		}
	}
	hx := proofHash("O", "C")
	gs := types.DefaultGenesis()
	gs.Proofs = []types.PaymentProof{{Hash: hx, Attestations: []string{g, g}}}
	if gs.Validate() == nil {
		t.Error("duplicate attestor in genesis accepted")
	}
	gs.Proofs = []types.PaymentProof{{Hash: "xyz"}}
	if gs.Validate() == nil {
		t.Error("bad proof hash accepted")
	}
	gs.Proofs = []types.PaymentProof{{Hash: hx, Status: types.PROOF_STATUS_CONFIRMED}}
	if gs.Validate() == nil {
		t.Error("confirmed proof without height accepted")
	}
	gs.Proofs = []types.PaymentProof{{Hash: hx, Status: types.PROOF_STATUS_CONFIRMED, ConfirmedHeight: 3, Attestations: []string{g}}}
	if err := gs.Validate(); err != nil {
		t.Error(err)
	}
}
