package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"

	"cosmossdk.io/client/v2/autocli"
	"cosmossdk.io/core/appmodule"
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
	addresscodec "github.com/cosmos/cosmos-sdk/codec/address"
	"github.com/cosmos/cosmos-sdk/crypto/keys/secp256k1"
	runtimeservices "github.com/cosmos/cosmos-sdk/runtime/services"
	"github.com/cosmos/cosmos-sdk/server"
	svrcmd "github.com/cosmos/cosmos-sdk/server/cmd"
	serverconfig "github.com/cosmos/cosmos-sdk/server/config"
	servertypes "github.com/cosmos/cosmos-sdk/server/types"
	simtestutil "github.com/cosmos/cosmos-sdk/testutil/sims"
	sdk "github.com/cosmos/cosmos-sdk/types"
	authcmd "github.com/cosmos/cosmos-sdk/x/auth/client/cli"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"
	bankcli "github.com/cosmos/cosmos-sdk/x/bank/client/cli"
	genutilcli "github.com/cosmos/cosmos-sdk/x/genutil/client/cli"
	"github.com/spf13/cast"
	"github.com/spf13/cobra"

	"github.com/joeylukoji/aurix/chain/app"
	smcli "github.com/joeylukoji/aurix/chain/x/smartmoney/client/cli"
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
	// AutoCLI generates the staking, slashing, distribution and evidence commands
	// (create-validator, delegate, unbond, unjail, withdraw-rewards, queries...), so
	// operators can join and manage validators after genesis. Hand-written commands
	// (bank send, smartmoney) take precedence.
	if err := autoCLIOptions(initCtx).EnhanceRootCommand(root); err != nil {
		panic(err)
	}
	return root
}

func queryCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "query", Aliases: []string{"q"}, Short: "Querying subcommands", RunE: client.ValidateCmd}
	cmd.AddCommand(rpc.QueryEventForTxCmd(), rpc.ValidatorCommand(), server.QueryBlockCmd(),
		authcmd.QueryTxsByEventsCmd(), server.QueryBlocksCmd(), authcmd.QueryTxCmd(), smcli.GetQueryCmd())
	return cmd
}

func txCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "tx", Short: "Transactions subcommands", RunE: client.ValidateCmd}
	cmd.AddCommand(authcmd.GetSignCommand(), authcmd.GetBroadcastCommand(), authcmd.GetEncodeCommand(),
		authcmd.GetDecodeCommand(), authcmd.GetSimulateCmd(), smcli.GetTxCmd())
	bank := &cobra.Command{Use: "bank", Short: "Bank transactions", RunE: client.ValidateCmd}
	bank.AddCommand(bankcli.NewSendTxCmd(addresscodec.NewBech32Codec(app.Bech32Prefix)))
	cmd.AddCommand(bank)
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

func autoCLIOptions(clientCtx client.Context) autocli.AppOptions {
	tmp := app.New(log.NewNopLogger(), dbm.NewMemDB(), nil, false, simtestutil.EmptyAppOptions{})
	modules := map[string]appmodule.AppModule{}
	for name, m := range tmp.ModuleManager.Modules {
		if am, ok := m.(appmodule.AppModule); ok {
			modules[name] = am
		}
	}
	return autocli.AppOptions{
		Modules:               modules,
		ModuleOptions:         runtimeservices.ExtractAutoCLIOptions(tmp.ModuleManager.Modules),
		AddressCodec:          addresscodec.NewBech32Codec(app.Bech32Prefix),
		ValidatorAddressCodec: addresscodec.NewBech32Codec(app.Bech32Prefix + "valoper"),
		ConsensusAddressCodec: addresscodec.NewBech32Codec(app.Bech32Prefix + "valcons"),
		ClientCtx:             clientCtx,
	}
}
