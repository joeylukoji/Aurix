package keeper

import (
	"context"
	"encoding/hex"
	"errors"
	"slices"
	"strconv"
	"strings"

	"cosmossdk.io/collections"
	errorsmod "cosmossdk.io/errors"
	sdk "github.com/cosmos/cosmos-sdk/types"
	sdkerrors "github.com/cosmos/cosmos-sdk/types/errors"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

type msgServer struct{ Keeper }

func NewMsgServerImpl(k Keeper) types.MsgServer { return msgServer{k} }

var _ types.MsgServer = msgServer{}

func (k msgServer) CreateEscrow(goCtx context.Context, msg *types.MsgCreateEscrow) (*types.MsgCreateEscrowResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "sender")
	}
	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "recipient")
	}
	if msg.Arbiter != "" {
		if _, err := sdk.AccAddressFromBech32(msg.Arbiter); err != nil {
			return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "arbiter")
		}
	}
	if msg.Amount.Denom != types.Denom || !msg.Amount.Amount.IsPositive() {
		return nil, errorsmod.Wrapf(types.ErrInvalidAmount, "must be a positive amount of %s", types.Denom)
	}
	if msg.DeadlineHeight <= ctx.BlockHeight() {
		return nil, types.ErrInvalidDeadline
	}
	if err := k.bank.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, sdk.NewCoins(msg.Amount)); err != nil {
		return nil, err
	}
	id, err := k.NextEscrowID.Next(ctx)
	if err != nil {
		return nil, err
	}
	e := types.Escrow{Id: id, Sender: msg.Sender, Recipient: msg.Recipient, Arbiter: msg.Arbiter,
		Amount: msg.Amount, DeadlineHeight: msg.DeadlineHeight, Status: types.ESCROW_STATUS_LOCKED,
		CreatedHeight: ctx.BlockHeight()}
	if err := k.Escrows.Set(ctx, id, e); err != nil {
		return nil, err
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent("escrow_created",
		sdk.NewAttribute("escrow_id", strconv.FormatUint(id, 10)),
		sdk.NewAttribute("sender", msg.Sender), sdk.NewAttribute("recipient", msg.Recipient),
		sdk.NewAttribute("amount", msg.Amount.String())))
	return &types.MsgCreateEscrowResponse{Id: id}, nil
}

func (k msgServer) loadLocked(ctx context.Context, id uint64) (types.Escrow, error) {
	e, err := k.Escrows.Get(ctx, id)
	if errors.Is(err, collections.ErrNotFound) {
		return e, types.ErrEscrowNotFound
	}
	if err != nil {
		return e, err
	}
	if e.Status != types.ESCROW_STATUS_LOCKED {
		return e, errorsmod.Wrapf(types.ErrNotLocked, "escrow %d is %s", id, e.Status)
	}
	return e, nil
}

func (k msgServer) settle(ctx sdk.Context, e types.Escrow, to string, status types.EscrowStatus, event string) error {
	addr, err := sdk.AccAddressFromBech32(to)
	if err != nil {
		return err
	}
	if err := k.bank.SendCoinsFromModuleToAccount(ctx, types.ModuleName, addr, sdk.NewCoins(e.Amount)); err != nil {
		return err
	}
	e.Status = status
	if err := k.Escrows.Set(ctx, e.Id, e); err != nil {
		return err
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(event,
		sdk.NewAttribute("escrow_id", strconv.FormatUint(e.Id, 10)),
		sdk.NewAttribute("recipient", to), sdk.NewAttribute("amount", e.Amount.String())))
	return nil
}

func (k msgServer) ReleaseEscrow(goCtx context.Context, msg *types.MsgReleaseEscrow) (*types.MsgReleaseEscrowResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	e, err := k.loadLocked(ctx, msg.EscrowId)
	if err != nil {
		return nil, err
	}
	if msg.Signer != e.Sender && msg.Signer != e.Arbiter {
		return nil, errorsmod.Wrap(types.ErrUnauthorized, "only sender or arbiter can release")
	}
	return &types.MsgReleaseEscrowResponse{}, k.settle(ctx, e, e.Recipient, types.ESCROW_STATUS_RELEASED, "escrow_released")
}

func (k msgServer) RefundEscrow(goCtx context.Context, msg *types.MsgRefundEscrow) (*types.MsgRefundEscrowResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	e, err := k.loadLocked(ctx, msg.EscrowId)
	if err != nil {
		return nil, err
	}
	isArbiter := e.Arbiter != "" && msg.Signer == e.Arbiter
	if msg.Signer != e.Sender && !isArbiter {
		return nil, errorsmod.Wrap(types.ErrUnauthorized, "only sender or arbiter can refund")
	}
	// The sender may refund only once the deadline height is reached; the arbiter may cancel anytime.
	if !isArbiter && ctx.BlockHeight() < e.DeadlineHeight {
		return nil, errorsmod.Wrapf(types.ErrDeadlineNotMet, "height %d < deadline %d", ctx.BlockHeight(), e.DeadlineHeight)
	}
	return &types.MsgRefundEscrowResponse{}, k.settle(ctx, e, e.Sender, types.ESCROW_STATUS_REFUNDED, "escrow_refunded")
}

func (k msgServer) AnchorProof(goCtx context.Context, msg *types.MsgAnchorProof) (*types.MsgAnchorProofResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	params, err := k.Params.Get(ctx)
	if err != nil {
		return nil, err
	}
	if !slices.Contains(params.AnchorSigners, msg.Signer) {
		return nil, errorsmod.Wrap(types.ErrUnauthorized, "signer not allowed to anchor proofs")
	}
	h := strings.ToLower(msg.Hash)
	if b, err := hex.DecodeString(h); err != nil || len(b) != 32 {
		return nil, types.ErrInvalidHash
	}
	threshold := params.AnchorThreshold
	if threshold == 0 {
		threshold = 1
	}
	p, err := k.Proofs.Get(ctx, h)
	switch {
	case errors.Is(err, collections.ErrNotFound):
		p = types.PaymentProof{Hash: h, ExternalRef: msg.ExternalRef, CaptureRef: msg.CaptureRef,
			Height: ctx.BlockHeight(), FirstSeenHeight: ctx.BlockHeight(), Submitter: msg.Signer,
			Status: types.PROOF_STATUS_PENDING}
	case err != nil:
		return nil, err
	default:
		if slices.Contains(p.Attestations, msg.Signer) {
			return nil, types.ErrDuplicateAttestation
		}
		if p.ExternalRef != msg.ExternalRef || p.CaptureRef != msg.CaptureRef {
			return nil, types.ErrProofMismatch
		}
	}
	p.Attestations = append(p.Attestations, msg.Signer)
	confirmedNow := false
	if p.Status != types.PROOF_STATUS_CONFIRMED && uint32(len(p.Attestations)) >= threshold {
		p.Status = types.PROOF_STATUS_CONFIRMED
		p.ConfirmedHeight = ctx.BlockHeight()
		confirmedNow = true
	}
	if err := k.Proofs.Set(ctx, h, p); err != nil {
		return nil, err
	}
	n := uint32(len(p.Attestations))
	ctx.EventManager().EmitEvent(sdk.NewEvent("proof_attested",
		sdk.NewAttribute("hash", h), sdk.NewAttribute("attestor", msg.Signer),
		sdk.NewAttribute("attestations", strconv.FormatUint(uint64(n), 10)),
		sdk.NewAttribute("threshold", strconv.FormatUint(uint64(threshold), 10))))
	if confirmedNow {
		ctx.EventManager().EmitEvent(sdk.NewEvent("proof_confirmed",
			sdk.NewAttribute("hash", h), sdk.NewAttribute("height", strconv.FormatInt(p.ConfirmedHeight, 10))))
	}
	return &types.MsgAnchorProofResponse{Status: p.Status, Attestations: n}, nil
}

func (k msgServer) UpdateParams(goCtx context.Context, msg *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if msg.Authority != k.authority {
		return nil, errorsmod.Wrapf(types.ErrInvalidAuthority, "expected %s, got %s", k.authority, msg.Authority)
	}
	if err := msg.Params.Validate(); err != nil {
		return nil, errorsmod.Wrap(types.ErrInvalidParams, err.Error())
	}
	if err := k.Params.Set(goCtx, msg.Params); err != nil {
		return nil, err
	}
	return &types.MsgUpdateParamsResponse{}, nil
}
