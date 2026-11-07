// Package keeper implements x/smartmoney: a finite-state escrow
// (LOCKED -> RELEASED | REFUNDED) driven by block height, and anchoring of
// external payment proofs. Locked funds are held by the module account.
package keeper

import (
	"context"

	"cosmossdk.io/collections"
	"cosmossdk.io/core/store"
	"github.com/cosmos/cosmos-sdk/codec"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

type Keeper struct {
	cdc          codec.BinaryCodec
	bank         types.BankKeeper
	Schema       collections.Schema
	Params       collections.Item[types.Params]
	Escrows      collections.Map[uint64, types.Escrow]
	NextEscrowID collections.Sequence
	Proofs       collections.Map[string, types.PaymentProof]
}

func NewKeeper(cdc codec.BinaryCodec, storeService store.KVStoreService, bank types.BankKeeper) Keeper {
	sb := collections.NewSchemaBuilder(storeService)
	k := Keeper{
		cdc:          cdc,
		bank:         bank,
		Params:       collections.NewItem(sb, types.ParamsKey, "params", codec.CollValue[types.Params](cdc)),
		Escrows:      collections.NewMap(sb, types.EscrowsKey, "escrows", collections.Uint64Key, codec.CollValue[types.Escrow](cdc)),
		NextEscrowID: collections.NewSequence(sb, types.NextEscrowIDKey, "next_escrow_id"),
		Proofs:       collections.NewMap(sb, types.ProofsKey, "proofs", collections.StringKey, codec.CollValue[types.PaymentProof](cdc)),
	}
	schema, err := sb.Build()
	if err != nil {
		panic(err)
	}
	k.Schema = schema
	return k
}

func (k Keeper) InitGenesis(ctx context.Context, gs types.GenesisState) error {
	if err := k.Params.Set(ctx, gs.Params); err != nil {
		return err
	}
	next := gs.NextEscrowId
	if next == 0 {
		next = 1
	}
	if err := k.NextEscrowID.Set(ctx, next); err != nil {
		return err
	}
	for _, e := range gs.Escrows {
		if err := k.Escrows.Set(ctx, e.Id, e); err != nil {
			return err
		}
	}
	for _, p := range gs.Proofs {
		if err := k.Proofs.Set(ctx, p.Hash, p); err != nil {
			return err
		}
	}
	return nil
}

func (k Keeper) ExportGenesis(ctx context.Context) (*types.GenesisState, error) {
	params, err := k.Params.Get(ctx)
	if err != nil {
		return nil, err
	}
	next, err := k.NextEscrowID.Peek(ctx)
	if err != nil {
		return nil, err
	}
	gs := &types.GenesisState{Params: params, NextEscrowId: next}
	err = k.Escrows.Walk(ctx, nil, func(_ uint64, e types.Escrow) (bool, error) {
		gs.Escrows = append(gs.Escrows, e)
		return false, nil
	})
	if err != nil {
		return nil, err
	}
	err = k.Proofs.Walk(ctx, nil, func(_ string, p types.PaymentProof) (bool, error) {
		gs.Proofs = append(gs.Proofs, p)
		return false, nil
	})
	return gs, err
}

// LockedTotal sums LOCKED escrows (invariant 2: equals the module account balance).
func (k Keeper) LockedTotal(ctx context.Context) (uint64, error) {
	var total uint64
	err := k.Escrows.Walk(ctx, nil, func(_ uint64, e types.Escrow) (bool, error) {
		if e.Status == types.ESCROW_STATUS_LOCKED {
			total += e.Amount.Amount.Uint64()
		}
		return false, nil
	})
	return total, err
}
