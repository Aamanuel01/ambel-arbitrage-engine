// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IFlashLoanSimpleReceiver} from "./interfaces/IFlashLoanSimpleReceiver.sol";
import {IPoolAddressesProvider} from "./interfaces/IPoolAddressesProvider.sol";
import {IPool} from "./interfaces/IPool.sol";
import {ISwapRouter} from "./interfaces/ISwapRouter.sol";
import {IUniswapV2Router02} from "./interfaces/IUniswapV2Router02.sol";

/// @title  ArbitrageBot
/// @notice Executes a flash-loan-funded arbitrage between Uniswap V3 and SushiSwap on Polygon.
///         The Python bot detects price differences, encodes the full routing payload, and calls
///         `requestFlashLoan`. Aave delivers the funds atomically and triggers `executeOperation`.
///         If the trade is unprofitable the transaction reverts — no capital is lost beyond gas.
/// @dev    DEX router addresses are passed dynamically via `dexPayload` (not hardcoded) so the
///         Python bot can route to any exchange without a contract redeployment.
///         Polygon Aave V3 Address Provider: 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb
contract ArbitrageBot is IFlashLoanSimpleReceiver, Ownable {
    using SafeERC20 for IERC20;

    // ── Immutables ────────────────────────────────────────────────────────────────
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    IPool public immutable POOL;

    // ── Custom errors ─────────────────────────────────────────────────────────────
    error NotOwner();
    error NotAavePool();
    error UnexpectedInitiator();
    error InsufficientRepayBalance(uint256 balance, uint256 required);
    error BelowMinProfit(uint256 profit, uint256 minProfit);

    // ── Events ────────────────────────────────────────────────────────────────────
    event ArbitrageExecuted(
        address indexed asset,
        uint256 loanAmount,
        uint256 profit,
        address indexed dexBuy,
        address indexed dexSell
    );

    // ── Constructor ───────────────────────────────────────────────────────────────

    /**
     * @dev Initialize the Aave V3 Pool Provider and set the bot owner.
     * @param _addressProvider The Aave V3 Pool Addresses Provider.
     *                         Polygon: 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb
     */
    constructor(address _addressProvider) Ownable(msg.sender) {
        ADDRESSES_PROVIDER = IPoolAddressesProvider(_addressProvider);
        POOL = IPool(ADDRESSES_PROVIDER.getPool());
    }

    // ── Owner entry-point ─────────────────────────────────────────────────────────

    /**
     * @dev Step 1: The Python bot calls this function to initiate the flash loan.
     * @param asset      The address of the token to borrow (e.g., USDC).
     * @param amount     The amount to borrow (in asset's native decimals).
     * @param dexPayload ABI-encoded routing data from the Python bot:
     *                   (address tokenB, address dexA, address dexB,
     *                    uint256 minProfit, uint24 feeA, uint24 feeB, bool uniFirst)
     *                   dexA = buy exchange, dexB = sell exchange.
     *                   uniFirst = true  → buy on Uniswap V3, sell on SushiSwap
     *                   uniFirst = false → buy on SushiSwap, sell on Uniswap V3
     */
    function requestFlashLoan(address asset, uint256 amount, bytes calldata dexPayload) external onlyOwner {
        POOL.flashLoanSimple(address(this), asset, amount, dexPayload, 0);
    }

    // ── Aave callback ─────────────────────────────────────────────────────────────

    /**
     * @dev Step 2: Aave calls this function AFTER sending the borrowed funds.
     *      Decodes the Python bot's routing payload, executes the two-leg swap,
     *      verifies profitability, repays Aave, and forwards the profit to the owner.
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(POOL)) revert NotAavePool();
        if (initiator != address(this)) revert UnexpectedInitiator();

        // 1. Decode the routing data passed from the Python bot
        (
            address tokenB,
            address dexA,      // buy exchange (cheaper price)
            address dexB,      // sell exchange (higher price)
            uint256 minProfit,
            uint24 feeA,       // Uniswap V3 pool fee tier for the buy leg
            uint24 feeB,       // Uniswap V3 pool fee tier for the sell leg
            bool uniFirst      // true = buy on Uniswap V3 / sell on SushiSwap
        ) = abi.decode(params, (address, address, address, uint256, uint24, uint24, bool));

        uint256 amountOwed = amount + premium;
        uint256 tokenBReceived;

        if (uniFirst) {
            // 2a. Buy tokenB with asset on Uniswap V3 (dexA)
            tokenBReceived = _swapOnUniswapV3(dexA, asset, tokenB, amount, feeA, 0);
            // 3a. Sell tokenB for asset on SushiSwap (dexB)
            _swapOnSushiSwap(dexB, tokenB, asset, tokenBReceived, amountOwed);
        } else {
            // 2b. Buy tokenB with asset on SushiSwap (dexA)
            tokenBReceived = _swapOnSushiSwap(dexA, asset, tokenB, amount, 0);
            // 3b. Sell tokenB for asset on Uniswap V3 (dexB)
            _swapOnUniswapV3(dexB, tokenB, asset, tokenBReceived, feeB, amountOwed);
        }

        // 4. Verify we can repay Aave (principal + 0.05% fee)
        uint256 balance = IERC20(asset).balanceOf(address(this));
        if (balance < amountOwed) revert InsufficientRepayBalance(balance, amountOwed);

        uint256 profit = balance - amountOwed;
        if (profit < minProfit) revert BelowMinProfit(profit, minProfit);

        // 5. Approve Aave to pull back the owed amount
        IERC20(asset).safeApprove(address(POOL), 0);
        IERC20(asset).safeApprove(address(POOL), amountOwed);

        // 6. Forward profit to owner
        if (profit > 0) {
            IERC20(asset).safeTransfer(owner(), profit);
        }

        emit ArbitrageExecuted(asset, amount, profit, dexA, dexB);
        return true;
    }

    // ── Internal swap helpers ─────────────────────────────────────────────────────

    /// @dev Execute an exact-input single-hop swap on Uniswap V3.
    ///      amountOutMinimum = 0 accepts any output (used for the buy leg in simulation).
    function _swapOnUniswapV3(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24 fee,
        uint256 amountOutMinimum
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).safeApprove(router, 0);
        IERC20(tokenIn).safeApprove(router, amountIn);

        ISwapRouter.ExactInputSingleParams memory swapParams = ISwapRouter.ExactInputSingleParams({
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            fee: fee,
            recipient: address(this),
            deadline: block.timestamp,
            amountIn: amountIn,
            amountOutMinimum: amountOutMinimum,
            sqrtPriceLimitX96: 0
        });

        amountOut = ISwapRouter(router).exactInputSingle(swapParams);
    }

    /// @dev Execute an exact-input swap on SushiSwap (Uniswap V2-style AMM).
    ///      amountOutMinimum = 0 accepts any output (used for the buy leg in simulation).
    function _swapOnSushiSwap(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMinimum
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).safeApprove(router, 0);
        IERC20(tokenIn).safeApprove(router, amountIn);

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        uint256[] memory amounts = IUniswapV2Router02(router).swapExactTokensForTokens(
            amountIn,
            amountOutMinimum,
            path,
            address(this),
            block.timestamp
        );

        amountOut = amounts[amounts.length - 1];
    }

    // ── Admin ─────────────────────────────────────────────────────────────────────

    /**
     * @dev Step 3: Withdraw ERC-20 profits (or any accidentally sent tokens) to the owner.
     */
    function withdrawProfits(address tokenAddress) external onlyOwner {
        IERC20 token = IERC20(tokenAddress);
        uint256 balance = token.balanceOf(address(this));
        require(balance > 0, "No profits to withdraw");
        token.safeTransfer(owner(), balance);
    }

    /**
     * @dev Withdraw native gas tokens (MATIC/POL) accidentally sent to the contract.
     */
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "No native balance");
        (bool success,) = payable(owner()).call{value: balance}("");
        require(success, "Transfer failed");
    }

    // Receive native tokens sent directly to the contract
    receive() external payable {}
}
