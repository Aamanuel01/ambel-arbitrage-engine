// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console} from "forge-std/Script.sol";
import {ArbitrageExecutor} from "../src/ArbitrageExecutor.sol";

/// @notice Deployment script for ArbitrageExecutor.
///         Usage:
///   forge script script/Deploy.s.sol \
///     --rpc-url $POLYGON_WS_RPC_URL \
///     --private-key $DEPLOYER_PRIVATE_KEY \
///     --broadcast
contract DeployArbitrageExecutor is Script {
    // ── Polygon Mainnet Addresses ─────────────────────────────────────────────────
    address constant AAVE_V3_POOL        = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant AAVE_ADDRESSES_PROV = 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address constant UNISWAP_V3_ROUTER   = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address constant SUSHI_ROUTER        = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        vm.startBroadcast(deployerKey);

        ArbitrageExecutor executor = new ArbitrageExecutor(
            AAVE_V3_POOL,
            AAVE_ADDRESSES_PROV,
            UNISWAP_V3_ROUTER,
            SUSHI_ROUTER
        );

        console.log("ArbitrageExecutor deployed at:", address(executor));

        vm.stopBroadcast();
    }
}
