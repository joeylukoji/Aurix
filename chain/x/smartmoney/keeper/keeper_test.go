package keeper_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"testing"

	"cosmossdk.io/math"
	storetypes "cosmossdk.io/store/types"
	"github.com/cosmos/cosmos-sdk/runtime"
	"github.com/cosmos/cosmos-sdk/testutil"
	sdk "github.com/cosmos/cosmos-sdk/types"
	moduletestutil "github.com/cosmos/cosmos-sdk/types/module/testutil"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"

	"github.com/joeylukoji/aurix/chain/x/smartmoney"
	"github.com/joeylukoji/aurix/chain/x/smartmoney/keeper"
	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

// fakeBank is an in-memory bank keeping a constant total supply.
type fakeBank struct{ bal map[string]math.Int }

func (b *fakeBank) move(from, to string, c sdk.Coins) error {
	amt := c.AmountOf(types.Denom)
	if b.get(from).LT(amt) {
		return fmt.Errorf("insufficient funds")
	}
	b.bal[from] = b.get(from).Sub(amt)
	b.bal[to] = b.get(to).Add(amt)
	return nil
}
func (b *fakeBank) get(a string) math.Int {
	if v, ok := b.bal[a]; ok {
		return v
	}
	return math.ZeroInt()
}
func (b *fakeBank) SendCoinsFromAccountToModule(_ context.Context, s sdk.AccAddress, m string, c sdk.Coins) error {
	return b.move(s.String(), authtypes.NewModuleAddress(m).String(), c)
}
func (b *fakeBank) SendCoinsFromModuleToAccount(_ context.Context, m string, r sdk.AccAddress, c sdk.Coins) error {
	return b.move(authtypes.NewModuleAddress(m).String(), r.String(), c)
}
func (b *fakeBank) GetBalance(_ context.Context, a sdk.AccAddress, d string) sdk.Coin {
	return sdk.NewCoin(d, b.get(a.String()))
}
func (b *fakeBank) total() math.Int {
	t := math.ZeroInt()
	for _, v := range b.bal {
		t = t.Add(v)
	}
	return t
}

type env struct {
	t                     *testing.T
	ctx                   sdk.Context
	k                     keeper.Keeper
	srv                   types.MsgServer
	bank                  *fakeBank
	alice, bob, arb, gate string
	supply                math.Int
}

func addr(s string) string { return sdk.AccAddress([]byte(fmt.Sprintf("%-20s", s))).String() }

func setup(t *testing.T) *env {
	key := storetypes.NewKVStoreKey(types.StoreKey)
	tctx := testutil.DefaultContextWithDB(t, key, storetypes.NewTransientStoreKey("t"))
	enc := moduletestutil.MakeTestEncodingConfig(smartmoney.AppModule{})
	e := &env{t: t, ctx: tctx.Ctx.WithBlockHeight(1), bank: &fakeBank{bal: map[string]math.Int{}},
		alice: addr("alice"), bob: addr("bob"), arb: addr("arbiter"), gate: addr("gateway")}
	e.k = keeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(key), e.bank, govAuthority)
	e.srv = keeper.NewMsgServerImpl(e.k)
	e.bank.bal[e.alice] = math.NewInt(100_000_000)
	e.supply = e.bank.total()
	gs := types.DefaultGenesis()
	gs.Params.AnchorSigners = []string{e.gate}
	if err := e.k.InitGenesis(e.ctx, *gs); err != nil {
		t.Fatal(err)
	}
	return e
}

func (e *env) height(h int64) { e.ctx = e.ctx.WithBlockHeight(h) }
func coin(n int64) sdk.Coin    { return sdk.NewInt64Coin(types.Denom, n) }

// Invariants 1 & 2: supply constant; module balance == sum of LOCKED escrows.
func (e *env) invariants() {
	e.t.Helper()
	if !e.bank.total().Equal(e.supply) {
		e.t.Fatalf("supply changed: %s != %s", e.bank.total(), e.supply)
	}
	locked, err := e.k.LockedTotal(e.ctx)
	if err != nil {
		e.t.Fatal(err)
	}
	mod := e.bank.get(authtypes.NewModuleAddress(types.ModuleName).String())
	if mod.Uint64() != locked {
		e.t.Fatalf("module balance %s != locked %d", mod, locked)
	}
}

func (e *env) create(amt, deadline int64, arbiter string) (uint64, error) {
	r, err := e.srv.CreateEscrow(e.ctx, &types.MsgCreateEscrow{Sender: e.alice, Recipient: e.bob,
		Arbiter: arbiter, Amount: coin(amt), DeadlineHeight: deadline})
	if err != nil {
		return 0, err
	}
	return r.Id, nil
}

func TestEscrowRelease(t *testing.T) {
	e := setup(t)
	id, err := e.create(5_000_000, 10, e.arb)
	if err != nil || id != 1 {
		t.Fatal(id, err)
	}
	e.invariants()
	if _, err := e.srv.ReleaseEscrow(e.ctx, &types.MsgReleaseEscrow{Signer: e.bob, EscrowId: id}); err == nil {
		t.Fatal("recipient must not release")
	}
	if _, err := e.srv.ReleaseEscrow(e.ctx, &types.MsgReleaseEscrow{Signer: e.arb, EscrowId: id}); err != nil {
		t.Fatal(err)
	}
	if e.bank.get(e.bob).Int64() != 5_000_000 {
		t.Fatal("bob not paid")
	}
	// invariant 3: no double release, no refund after release
	if _, err := e.srv.ReleaseEscrow(e.ctx, &types.MsgReleaseEscrow{Signer: e.arb, EscrowId: id}); err == nil {
		t.Fatal("double release")
	}
	e.height(100)
	if _, err := e.srv.RefundEscrow(e.ctx, &types.MsgRefundEscrow{Signer: e.alice, EscrowId: id}); err == nil {
		t.Fatal("refund after release")
	}
	e.invariants()
}

func TestEscrowRefundAfterDeadline(t *testing.T) {
	e := setup(t)
	id, _ := e.create(1_000, 5, "")
	if _, err := e.srv.RefundEscrow(e.ctx, &types.MsgRefundEscrow{Signer: e.alice, EscrowId: id}); err == nil {
		t.Fatal("refund before deadline")
	}
	e.height(5)
	if _, err := e.srv.RefundEscrow(e.ctx, &types.MsgRefundEscrow{Signer: e.alice, EscrowId: id}); err != nil {
		t.Fatal(err)
	}
	if _, err := e.srv.ReleaseEscrow(e.ctx, &types.MsgReleaseEscrow{Signer: e.alice, EscrowId: id}); err == nil {
		t.Fatal("release after refund")
	}
	got, _ := e.k.Escrows.Get(e.ctx, id)
	if got.Status != types.ESCROW_STATUS_REFUNDED || !e.bank.get(e.alice).Equal(math.NewInt(100_000_000)) {
		t.Fatal(got.Status, e.bank.get(e.alice))
	}
	e.invariants()
}

func TestArbiterCanCancel(t *testing.T) {
	e := setup(t)
	id, _ := e.create(1_000, 50, e.arb)
	if _, err := e.srv.RefundEscrow(e.ctx, &types.MsgRefundEscrow{Signer: e.arb, EscrowId: id}); err != nil {
		t.Fatal(err)
	}
	e.invariants()
}

func TestCreateValidation(t *testing.T) {
	e := setup(t)
	if _, err := e.create(1, 1, ""); err == nil {
		t.Fatal("deadline at current height accepted")
	}
	if _, err := e.create(0, 10, ""); err == nil {
		t.Fatal("zero amount accepted")
	}
	if _, err := e.create(1_000_000_000, 10, ""); err == nil {
		t.Fatal("insufficient funds accepted")
	}
	if _, err := e.srv.CreateEscrow(e.ctx, &types.MsgCreateEscrow{Sender: e.alice, Recipient: e.bob,
		Amount: sdk.NewInt64Coin("stake", 1), DeadlineHeight: 10}); err == nil {
		t.Fatal("wrong denom accepted")
	}
	e.invariants()
}

func TestAnchorProof(t *testing.T) {
	e := setup(t)
	h := sha256.Sum256([]byte("order|capture"))
	hx := hex.EncodeToString(h[:])
	if _, err := e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: e.alice, Hash: hx}); err == nil {
		t.Fatal("unauthorised anchor")
	}
	if _, err := e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: e.gate, Hash: "abcd"}); err == nil {
		t.Fatal("bad hash")
	}
	if _, err := e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: e.gate, Hash: hx, ExternalRef: "paypal:1"}); err != nil {
		t.Fatal(err)
	}
	if _, err := e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: e.gate, Hash: hx}); err == nil {
		t.Fatal("duplicate proof")
	}
	if p, err := e.k.Proofs.Get(e.ctx, hx); err != nil || p.ExternalRef != "paypal:1" {
		t.Fatal(p, err)
	}
	e.invariants() // anchoring creates no AUR
}

func TestGenesisRoundTrip(t *testing.T) {
	e := setup(t)
	_, _ = e.create(1_000, 10, "")
	gs, err := e.k.ExportGenesis(e.ctx)
	if err != nil || len(gs.Escrows) != 1 || gs.NextEscrowId != 2 || gs.Validate() != nil {
		t.Fatal(gs, err)
	}
}
