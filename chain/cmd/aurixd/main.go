package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"

	"cosmossdk.io/log"
	cmtcfg "github.com/cometbft/cometbft/config"
	dbm "github.com/cosmos/cosmos-db"
	"github.com/cosmos/cosmos-sdk/baseapp"
	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/client/config"
	"github.com/cosmos/cosmos-sdk/client/debug"
	"github.com/cosmos/cosmos-sdk/client/flags"
	"github.com/cosmos/cosmos-sdk/client/keys"
	"github.com/cosmos/cosmos-sdk/client/rpc"
	"github.com/cosmos/cosmos-sdk/crypto/keys/secp256k1"
	"github.com/cosmos/cosmos-sdk/server"
	serverconfig "github.com/cosmos/cosmos-sdk/server/config"
	servertypes "github.com/cosmos/cosmos-sdk/server/types"
	svrcmd "github.com/cosmos/cosmos-sdk/server/cmd"
	sdk "github.com/cosmos/cosmos-sdk/types"
	authcmd "github.com/cosmos/cosmos-sdk/x/auth/client/cli"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"
	genutilcli "github.com/cosmos/cosmos-sdk/x/genutil/client/cli"
	"github.com/spf13/cast"
	"github.com/spf13/cobra"

	"github.com/joeylukoji/aurix/chain/app"
)

func main() {
	app.SetConfig()
	if err := svrcmd.Execute(rootCmd(), "AURIXD", app.DefaultNodeHome); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func rootCmd() *cobra.Command {
	enc := app.MakeEncodingConfig()
	initCtx := client.Context{}.
		WithCodec(enc.Codec).
		WithInterfaceRegistry(enc.InterfaceRegistry).
		WithTxConfig(enc.TxConfig).
		WithLegacyAmino(enc.Amino).
		WithInput(os.Stdin).
		WithAccountRetriever(authtypes.AccountRetriever{}).
		WithHomeDir(app.DefaultNodeHome).
		WithViper("AURIXD")

	root := &cobra.Command{
		Use:   "aurixd",
		Short: "AURIX node (Cosmos SDK + CometBFT)",
		PersistentPreRunE: func(cmd *cobra.Command, _ []string) error {
			cmd.SetOut(cmd.OutOrStdout())
			cmd.SetErr(cmd.ErrOrStderr())
			ctx, err := client.ReadPersistentCommandFlags(initCtx, cmd.Flags())
			if err != nil {
				return err
			}
			if ctx, err = config.ReadFromClientConfig(ctx); err != nil {
				return err
			}
			if err := client.SetCmdClientContextHandler(ctx, cmd); err != nil {
				return err
			}
			srvCfg := serverconfig.DefaultConfig()
			srvCfg.MinGasPrices = "0.0025" + app.BondDenom
			cmt := cmtcfg.DefaultConfig()
			cmt.Consensus.TimeoutCommit = 1_000_000_000 // 1s blocks for the demo
			return server.InterceptConfigsPreRunHandler(cmd, serverconfig.DefaultConfigTemplate, srvCfg, cmt)
		},
	}

	root.AddCommand(
		genutilcli.InitCmd(app.ModuleBasics, app.DefaultNodeHome),
		debug.Cmd(),
		seedAddressCmd(),
	)
	server.AddCommands(root, app.DefaultNodeHome, newApp, exportApp, func(*cobra.Command) {})
	root.AddCommand(
		server.StatusCommand(),
		genutilcli.Commands(enc.TxConfig, app.ModuleBasics, app.DefaultNodeHome),
		queryCmd(),
		txCmd(),
		keys.Commands(),
	)
	return root
}

func queryCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "query", Aliases: []string{"q"}, Short: "Querying subcommands", RunE: client.ValidateCmd}
	cmd.AddCommand(rpc.QueryEventForTxCmd(), rpc.ValidatorCommand(), server.QueryBlockCmd(),
		authcmd.QueryTxsByEventsCmd(), server.QueryBlocksCmd(), authcmd.QueryTxCmd())
	return cmd
}

func txCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "tx", Short: "Transactions subcommands", RunE: client.ValidateCmd}
	cmd.AddCommand(authcmd.GetSignCommand(), authcmd.GetBroadcastCommand(), authcmd.GetEncodeCommand(),
		authcmd.GetDecodeCommand(), authcmd.GetSimulateCmd())
	return cmd
}

// seedAddressCmd prints the address of the dev key derived as secp256k1(sha256(seed)),
// the same derivation the gateway and the web wallet use.
func seedAddressCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "seed-address [seed]",
		Short: "Print the aur1... address of the dev key derived from a seed string",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			h := sha256.Sum256([]byte(args[0]))
			pk := &secp256k1.PrivKey{Key: h[:]}
			fmt.Fprintln(cmd.OutOrStdout(), sdk.AccAddress(pk.PubKey().Address()).String())
			return nil
		},
	}
}

func newApp(logger log.Logger, db dbm.DB, traceStore io.Writer, opts servertypes.AppOptions) servertypes.Application {
	return app.New(logger, db, traceStore, true, opts, server.DefaultBaseappOptions(opts)...)
}

func exportApp(logger log.Logger, db dbm.DB, traceStore io.Writer, height int64, forZeroHeight bool,
	jailAllowedAddrs []string, opts servertypes.AppOptions, modulesToExport []string) (servertypes.ExportedApp, error) {
	homePath, ok := opts.Get(flags.FlagHome).(string)
	if !ok || homePath == "" {
		return servertypes.ExportedApp{}, errors.New("application home not set")
	}
	a := app.New(logger, db, traceStore, height == -1, opts, baseapp.SetChainID(cast.ToString(opts.Get(flags.FlagChainID))))
	if height != -1 {
		if err := a.LoadHeight(height); err != nil {
			return servertypes.ExportedApp{}, err
		}
	}
	return a.ExportAppStateAndValidators(forZeroHeight, jailAllowedAddrs, modulesToExport)
}
