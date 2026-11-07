package keeper

import (
	"context"
	"errors"
	"strings"

	"cosmossdk.io/collections"
	"github.com/cosmos/cosmos-sdk/types/query"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

type queryServer struct{ Keeper }

func NewQueryServerImpl(k Keeper) types.QueryServer { return queryServer{k} }

func notFound(err error, what string) error {
	if errors.Is(err, collections.ErrNotFound) {
		return status.Error(codes.NotFound, what+" not found")
	}
	return err
}

func (q queryServer) Escrow(ctx context.Context, r *types.QueryEscrowRequest) (*types.QueryEscrowResponse, error) {
	e, err := q.Keeper.Escrows.Get(ctx, r.Id)
	if err != nil {
		return nil, notFound(err, "escrow")
	}
	return &types.QueryEscrowResponse{Escrow: e}, nil
}

func (q queryServer) Escrows(ctx context.Context, _ *types.QueryEscrowsRequest) (*types.QueryEscrowsResponse, error) {
	out := &types.QueryEscrowsResponse{}
	err := q.Keeper.Escrows.Walk(ctx, nil, func(_ uint64, e types.Escrow) (bool, error) {
		out.Escrows = append(out.Escrows, e)
		return false, nil
	})
	return out, err
}

func (q queryServer) Proof(ctx context.Context, r *types.QueryProofRequest) (*types.QueryProofResponse, error) {
	p, err := q.Proofs.Get(ctx, strings.ToLower(r.Hash))
	if err != nil {
		return nil, notFound(err, "proof")
	}
	return &types.QueryProofResponse{Proof: p}, nil
}

func (q queryServer) PendingProofs(ctx context.Context, r *types.QueryPendingProofsRequest) (*types.QueryPendingProofsResponse, error) {
	proofs, page, err := query.CollectionFilteredPaginate(ctx, q.Proofs, r.Pagination,
		func(_ string, p types.PaymentProof) (bool, error) { return p.Status == types.PROOF_STATUS_PENDING, nil },
		func(_ string, p types.PaymentProof) (types.PaymentProof, error) { return p, nil })
	if err != nil {
		return nil, err
	}
	return &types.QueryPendingProofsResponse{Proofs: proofs, Pagination: page}, nil
}

func (q queryServer) Params(ctx context.Context, _ *types.QueryParamsRequest) (*types.QueryParamsResponse, error) {
	p, err := q.Keeper.Params.Get(ctx)
	return &types.QueryParamsResponse{Params: p}, err
}
