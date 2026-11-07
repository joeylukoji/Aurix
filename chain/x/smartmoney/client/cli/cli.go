// Package cli provides aurixd query/tx commands for x/smartmoney.
package cli

import (
	"strconv"

	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/client/flags"
	"github.com/cosmos/cosmos-sdk/client/tx"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/spf13/cobra"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

const FlagArbiter = "arbiter"

// GetQueryCmd returns `aurixd query smartmoney ...`.
func GetQueryCmd() *cobra.Command {
	cmd := &cobra.Command{Use: types.ModuleName, Short: "Query the smartmoney module", RunE: client.ValidateCmd}
	escrow := &cobra.Command{
		Use: "escrow [id]", Short: "Show an escrow", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, err := client.GetClientQueryContext(cmd)
			if err != nil {
				return err
			}
			id, err := strconv.ParseUint(args[0], 10, 64)
			if err != nil {
				return err
			}
			res, err := types.NewQueryClient(ctx).Escrow(cmd.Context(), &types.QueryEscrowRequest{Id: id})
			if err != nil {
				return err
			}
			return ctx.PrintProto(res)
		},
	}
	escrows := &cobra.Command{
		Use: "escrows", Short: "List all escrows", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, err := client.GetClientQueryContext(cmd)
			if err != nil {
				return err
			}
			res, err := types.NewQueryClient(ctx).Escrows(cmd.Context(), &types.QueryEscrowsRequest{})
			if err != nil {
				return err
			}
			return ctx.PrintProto(res)
		},
	}
	proof := &cobra.Command{
		Use: "proof [hash]", Short: "Show an anchored payment proof", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, err := client.GetClientQueryContext(cmd)
			if err != nil {
				return err
			}
			res, err := types.NewQueryClient(ctx).Proof(cmd.Context(), &types.QueryProofRequest{Hash: args[0]})
			if err != nil {
				return err
			}
			return ctx.PrintProto(res)
		},
	}
	params := &cobra.Command{
		Use: "params", Short: "Show module params", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, err := client.GetClientQueryContext(cmd)
			if err != nil {
				return err
			}
			res, err := types.NewQueryClient(ctx).Params(cmd.Context(), &types.QueryParamsRequest{})
			if err != nil {
				return err
			}
			return ctx.PrintProto(res)
		},
	}
	pending := &cobra.Command{
		Use: "pending-proofs", Short: "List payment proofs still awaiting attestations", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, err := client.GetClientQueryContext(cmd)
			if err != nil {
				return err
			}
			page, err := client.ReadPageRequest(cmd.Flags())
			if err != nil {
				return err
			}
			res, err := types.NewQueryClient(ctx).PendingProofs(cmd.Context(), &types.QueryPendingProofsRequest{Pagination: page})
			if err != nil {
				return err
			}
			return ctx.PrintProto(res)
		},
	}
	flags.AddPaginationFlagsToCmd(pending, "pending-proofs")
	for _, c := range []*cobra.Command{escrow, escrows, proof, pending, params} {
		flags.AddQueryFlagsToCmd(c)
		cmd.AddCommand(c)
	}
	return cmd
}

// GetTxCmd returns `aurixd tx smartmoney ...`.
func GetTxCmd() *cobra.Command {
	cmd := &cobra.Command{Use: types.ModuleName, Short: "smartmoney transactions", RunE: client.ValidateCmd}
	create := &cobra.Command{
		Use:   "create-escrow [recipient] [amount] [deadline-height]",
		Short: "Lock funds in escrow (e.g. 1000000uaur), refundable after deadline-height",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}
			coin, err := sdk.ParseCoinNormalized(args[1])
			if err != nil {
				return err
			}
			deadline, err := strconv.ParseInt(args[2], 10, 64)
			if err != nil {
				return err
			}
			arbiter, _ := cmd.Flags().GetString(FlagArbiter)
			msg := &types.MsgCreateEscrow{Sender: ctx.GetFromAddress().String(), Recipient: args[0],
				Arbiter: arbiter, Amount: coin, DeadlineHeight: deadline}
			return tx.GenerateOrBroadcastTxCLI(ctx, cmd.Flags(), msg)
		},
	}
	create.Flags().String(FlagArbiter, "", "optional arbiter address allowed to release/refund")
	release := idCmd("release-escrow [id]", "Release escrowed funds to the recipient", func(s string, id uint64) sdk.Msg {
		return &types.MsgReleaseEscrow{Signer: s, EscrowId: id}
	})
	refund := idCmd("refund-escrow [id]", "Refund escrowed funds to the sender", func(s string, id uint64) sdk.Msg {
		return &types.MsgRefundEscrow{Signer: s, EscrowId: id}
	})
	anchor := &cobra.Command{
		Use:   "anchor-proof [hash] [external-ref] [capture-ref] [amount] [currency]",
		Short: "Attest an external payment proof (signer must be an anchor signer)",
		Args:  cobra.ExactArgs(5),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}
			msg := &types.MsgAnchorProof{Signer: ctx.GetFromAddress().String(), Hash: args[0],
				ExternalRef: args[1], CaptureRef: args[2], Amount: args[3], Currency: args[4]}
			return tx.GenerateOrBroadcastTxCLI(ctx, cmd.Flags(), msg)
		},
	}
	for _, c := range []*cobra.Command{create, release, refund, anchor} {
		flags.AddTxFlagsToCmd(c)
		cmd.AddCommand(c)
	}
	return cmd
}

func idCmd(use, short string, mk func(string, uint64) sdk.Msg) *cobra.Command {
	return &cobra.Command{
		Use: use, Short: short, Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}
			id, err := strconv.ParseUint(args[0], 10, 64)
			if err != nil {
				return err
			}
			return tx.GenerateOrBroadcastTxCLI(ctx, cmd.Flags(), mk(ctx.GetFromAddress().String(), id))
		},
	}
}
